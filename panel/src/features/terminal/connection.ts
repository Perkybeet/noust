/**
 * One terminal session, from the person pressing Open to the shell ending.
 *
 * Opening is two steps, as the server has it: a call, inside sudo mode, that returns a ticket
 * (`POST /api/apps/{domain}/terminal`, `POST /api/server/terminal`), then a WebSocket that
 * redeems it. Everything between is here: what the screen shows is an emulator the shell's output
 * is written into, what the person types goes the other way, and the session's phase (opening,
 * connected, reconnecting, ended, failed) is what the page draws.
 *
 * A connection that drops is not the end of the shell: the server keeps it for a minute. This asks
 * for a new ticket that resumes the same session, with the backoff every stream of the console
 * uses, and gives up only when the server says the shell is gone or the minute has passed. What the
 * program printed meanwhile is delivered to the screen when it comes back.
 *
 * It is plain TypeScript with the network and the clock passed in, so every path (a refusal, a
 * limit warning, a drop and a return, a shell that ended while away) is tested without a browser;
 * `useTerminalSession` is the thin hook over it.
 */

import { isApiError, request } from "../../api/client";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n/translate";
import type { BodyOf, ResponseOf } from "../../api/client";
import type { ScreenSource } from "../../components/ui/terminal/controller";
import { Terminal } from "../../lib/terminal/terminal";
import { reconnectDelay } from "../../realtime/backoff";
import { WS_CLOSE_UNAUTHORIZED } from "../../realtime/sockets";
import type { SocketLike } from "../../realtime/sockets";

export type TerminalTarget = { kind: "app"; domain: string } | { kind: "server" };

export type Phase = "idle" | "opening" | "open" | "reconnecting" | "ended" | "failed";

/** A WebSocket as far as a terminal uses it. */
export interface TerminalSocket extends SocketLike {
  send: (data: string) => void;
}

/** What a ticket's call asks for beyond where. */
export interface OpenOptions {
  shell?: string | undefined;
  service?: string | undefined;
}

/** What the server said about the session when it opened. */
export interface SessionInfo {
  session: string;
  /** The place and the account, as the header says them. */
  target: { kind: string; domain: string | null; service: string | null; label: string; account: string };
  actor: string;
  idleSeconds: number;
  /** `Date.now()` at which the session ends however busy it is. */
  endsAt: number;
  recorded: boolean;
}

/** A warning the server sent: a limit is a minute away. */
export interface LimitNotice {
  kind: "idle" | "limit";
  /** `Date.now()` at which it happens. */
  at: number;
}

export interface Ended {
  reason: string;
  exitCode: number | null;
}

/** Why a session did not open: the server's own words, kept whole. */
export interface Failure {
  /** The API error, or `{ detail, hint }` for a socket's refusal: what `ErrorBlock` reads. */
  error: unknown;
  /** What the console branches on: `disabled`, `limit`, `ticket`, `forbidden`, `gone`, `unavailable`. */
  code: string | null;
}

export interface SessionState {
  phase: Phase;
  info: SessionInfo | null;
  notice: LimitNotice | null;
  ended: Ended | null;
  failure: Failure | null;
  /** The connection came back to the same shell. */
  resumed: boolean;
  /** Output was lost while it was away. */
  lost: boolean;
}

export const INITIAL_STATE: SessionState = {
  phase: "idle",
  info: null,
  notice: null,
  ended: null,
  failure: null,
  resumed: false,
  lost: false,
};

/** How long a dropped connection is tried again: the server keeps the shell a minute. */
export const RECONNECT_WINDOW_MS = 75_000;

/** The size a terminal is opened at until the screen has measured itself. */
const DEFAULT_SIZE = { cols: 80, rows: 24 };

type TicketBody = BodyOf<"/api/apps/{domain}/terminal", "post">;
type Ticket = ResponseOf<"/api/apps/{domain}/terminal", "post">;

/** What the connection needs from outside. */
export interface Dependencies {
  /** The server on screen, read when a socket is built. */
  node: () => string | null;
  /** The console's own single-use ticket, which authenticates every socket. */
  platformTicket: () => Promise<string>;
  /** The address of a socket on this origin, from its path and query. */
  address: (path: string, query: Record<string, string>) => string;
  /** The path of a stream on a node, when a node is on screen. */
  nodePath: (node: string | null, path: string) => string;
  connect: (url: string) => TerminalSocket;
  now: () => number;
}

function text(frame: Record<string, unknown>, key: string): string | null {
  const value = frame[key];
  return typeof value === "string" ? value : null;
}

function numeric(frame: Record<string, unknown>, key: string): number | null {
  const value = frame[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export class TerminalConnection {
  readonly target: TerminalTarget;
  private readonly deps: Dependencies;
  private state: SessionState = INITIAL_STATE;
  private readonly watchers = new Set<() => void>();
  private socket: TerminalSocket | null = null;
  private opened = false;
  private session: string | null = null;
  private droppedAt: number | null = null;
  private attempts = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private options: OpenOptions = {};
  /** Bumped whenever the session in hand changes, so a late answer for an old one is ignored. */
  private generation = 0;
  private current: ScreenSource & { notify: () => void };

  constructor(target: TerminalTarget, deps: Dependencies) {
    this.target = target;
    this.deps = deps;
    this.current = this.makeSource();
  }

  // -- what the page reads --------------------------------------------------------------

  getState = (): SessionState => this.state;

  /** The emulator and when it changes; a new one for each session. */
  getSource = (): ScreenSource => this.current;

  subscribe = (watcher: () => void): (() => void) => {
    this.watchers.add(watcher);
    return () => {
      this.watchers.delete(watcher);
    };
  };

  private set(next: Partial<SessionState>): void {
    this.state = { ...this.state, ...next };
    for (const watcher of this.watchers) watcher();
  }

  private makeSource(): ScreenSource & { notify: () => void } {
    const listeners = new Set<() => void>();
    // The emulator answers a program's queries (where the cursor is) as if the person typed them.
    const terminal = new Terminal(DEFAULT_SIZE.cols, DEFAULT_SIZE.rows, {
      onResponse: (data) => {
        this.frame({ type: "input", data });
      },
    });
    return {
      terminal,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
        };
      },
      notify: () => {
        for (const listener of listeners) listener();
      },
    };
  }

  // -- what the page asks ---------------------------------------------------------------

  /** Asks for a ticket and opens the session. */
  open(options: OpenOptions = {}): void {
    this.reset();
    this.options = options;
    this.set({ ...INITIAL_STATE, phase: "opening" });
    void this.mint(null, this.generation);
  }

  /** Ends the shell: it says so, and the socket's `ended` frame closes the page's state. */
  end(): void {
    if (this.opened) {
      this.frame({ type: "close" });
      return;
    }
    this.reset();
    this.set({ phase: "ended", notice: null, ended: { reason: "closed", exitCode: null } });
  }

  /** What was typed. Typing is what keeps a terminal from closing for idleness. */
  send(data: string): void {
    this.frame({ type: "input", data });
    if (this.state.notice?.kind === "idle") this.set({ notice: null });
  }

  resize(cols: number, rows: number): void {
    this.frame({ type: "resize", cols, rows });
  }

  /** Back to the start, with an empty screen: another terminal. */
  restart(): void {
    this.reset();
    this.current = this.makeSource();
    this.set({ ...INITIAL_STATE });
  }

  /** The page is gone: the connection ends, and the shell stays for the minute the server keeps it. */
  dispose(): void {
    this.reset();
  }

  // -- the connection -------------------------------------------------------------------

  private reset(): void {
    this.generation += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.drop();
    this.session = null;
    this.droppedAt = null;
    this.attempts = 0;
  }

  private drop(): void {
    const socket = this.socket;
    this.socket = null;
    this.opened = false;
    if (socket !== null) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.close(1000);
    }
  }

  private frame(data: Record<string, unknown>): void {
    if (this.socket !== null && this.opened) this.socket.send(JSON.stringify(data));
  }

  private async mint(resume: string | null, mine: number): Promise<void> {
    const screen = this.current.terminal.screen;
    const body: TicketBody = {
      rows: screen.rows,
      cols: screen.cols,
      ...(this.options.shell !== undefined ? { shell: this.options.shell } : {}),
      ...(this.options.service !== undefined ? { service: this.options.service } : {}),
      ...(resume !== null ? { resume } : {}),
    };
    try {
      const ticket =
        this.target.kind === "app"
          ? await request("post", "/api/apps/{domain}/terminal", { params: { domain: this.target.domain }, body })
          : await request("post", "/api/server/terminal", { body });
      if (mine !== this.generation) return;
      await this.connect(ticket, mine);
    } catch (error: unknown) {
      if (mine !== this.generation) return;
      if (resume !== null) {
        const gone = isApiError(error) && (error.status === 404 || error.status === 403);
        if (gone) this.set({ phase: "ended", notice: null, ended: { reason: "disconnected", exitCode: null } });
        else this.scheduleResume(mine);
        return;
      }
      this.set({ phase: "failed", failure: { error, code: isApiError(error) ? error.error : null } });
    }
  }

  private async connect(ticket: Ticket, mine: number): Promise<void> {
    let platform: string;
    try {
      platform = await this.deps.platformTicket();
    } catch (error: unknown) {
      if (mine === this.generation) this.set({ phase: "failed", failure: { error, code: null } });
      return;
    }
    if (mine !== this.generation) return;
    const url = this.deps.address(this.deps.nodePath(this.deps.node(), ticket.path), {
      ticket: platform,
      terminal_ticket: ticket.ticket,
    });
    const made = this.deps.connect(url);
    this.socket = made;
    made.onopen = () => {
      this.opened = true;
    };
    made.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== "string" || mine !== this.generation) return;
      try {
        const parsed: unknown = JSON.parse(event.data);
        if (typeof parsed === "object" && parsed !== null) this.handle(parsed as Record<string, unknown>);
      } catch {
        console.error("Ignored a malformed terminal frame:", event.data);
      }
    };
    made.onclose = (event: CloseEvent) => {
      if (mine !== this.generation || this.socket !== made) return;
      this.socket = null;
      this.opened = false;
      const phase = this.state.phase;
      if (phase === "ended" || phase === "failed" || phase === "idle") return;
      if (event.code === WS_CLOSE_UNAUTHORIZED) {
        this.set({ phase: "ended", notice: null, ended: { reason: "revoked", exitCode: null } });
        return;
      }
      if (phase === "opening") {
        const detail = event.reason !== "" ? event.reason : translate(getLocale(), "terminal.failed.connection");
        this.set({ phase: "failed", failure: { code: null, error: { detail } } });
        return;
      }
      this.scheduleResume(mine);
    };
  }

  private scheduleResume(mine: number): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.droppedAt ??= this.deps.now();
    if (this.deps.now() - this.droppedAt >= RECONNECT_WINDOW_MS || this.session === null) {
      this.set({ phase: "ended", notice: null, ended: { reason: "disconnected", exitCode: null } });
      return;
    }
    this.set({ phase: "reconnecting" });
    const delay = reconnectDelay(this.attempts);
    this.attempts += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (mine === this.generation && this.session !== null) void this.mint(this.session, mine);
    }, delay);
  }

  // -- what the server says -------------------------------------------------------------

  private handle(frame: Record<string, unknown>): void {
    switch (frame["type"]) {
      case "ready":
        this.ready(frame);
        return;
      case "output": {
        const data = text(frame, "data");
        if (data !== null) {
          this.current.terminal.write(data);
          this.current.notify();
        }
        return;
      }
      case "notice": {
        const seconds = numeric(frame, "seconds") ?? 60;
        this.set({ notice: { kind: frame["kind"] === "limit" ? "limit" : "idle", at: this.deps.now() + seconds * 1000 } });
        return;
      }
      case "ended":
        this.drop();
        this.set({ phase: "ended", notice: null, ended: { reason: text(frame, "reason") ?? "unknown", exitCode: numeric(frame, "exit_code") } });
        return;
      case "error": {
        const code = text(frame, "code");
        // Something typed was refused, or another window took the session: said where it is, and the shell goes on.
        if (this.state.phase === "open" && (code === "input" || code === null)) return;
        this.drop();
        this.set({ phase: "failed", failure: { code, error: { detail: text(frame, "message") ?? translate(getLocale(), "terminal.failed.unreachable"), hint: text(frame, "details") ?? undefined } } });
        return;
      }
      default:
        return;
    }
  }

  private ready(frame: Record<string, unknown>): void {
    const named = typeof frame["target"] === "object" && frame["target"] !== null ? (frame["target"] as Record<string, unknown>) : {};
    const info: SessionInfo = {
      session: text(frame, "session") ?? this.session ?? "",
      target: {
        kind: text(named, "kind") ?? "app",
        domain: text(named, "domain"),
        service: text(named, "service"),
        label: text(named, "label") ?? "",
        account: text(named, "account") ?? "",
      },
      actor: text(frame, "actor") ?? "",
      idleSeconds: numeric(frame, "idle_seconds") ?? 0,
      endsAt: this.deps.now() + (numeric(frame, "ends_in") ?? 0) * 1000,
      recorded: frame["recorded"] !== false,
    };
    this.session = info.session;
    this.droppedAt = null;
    this.attempts = 0;
    this.set({ phase: "open", info, failure: null, resumed: frame["resumed"] === true, lost: frame["lost"] === true });
    // The screen was measured before the session opened; the shell is told its size again to be sure.
    const screen = this.current.terminal.screen;
    this.frame({ type: "resize", cols: screen.cols, rows: screen.rows });
  }
}
