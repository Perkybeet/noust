/**
 * Stand-ins for a terminal's network, for tests: the ticket call, and a socket that records what
 * the console sends and says what the server would. They speak the wire format of
 * `noust/web/api/terminal.py` and `noust/web/websockets/terminal.py`.
 */

import { nodeSocketPath } from "../../api/nodeScope";
import { socketUrl } from "../../realtime/sockets";
import { json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { FakeWebSocket } from "../../test/fakes";
import type { Dependencies, TerminalSocket } from "./connection";

/** A WebSocket that also takes what the console types. */
export class FakeTerminalSocket extends FakeWebSocket implements TerminalSocket {
  readonly sent: string[] = [];

  send(data: string): void {
    this.sent.push(data);
  }

  /** What the console has sent, parsed. */
  frames(): Record<string, unknown>[] {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }

  /** What the console has typed, in order, as the shell would receive it. */
  typed(): string {
    return this.frames()
      .filter((frame) => frame["type"] === "input")
      .map((frame) => String(frame["data"]))
      .join("");
  }
}

export interface TicketOptions {
  domain?: string | null;
  session?: string;
  resume?: boolean;
  account?: string;
  idleSeconds?: number;
  maxSeconds?: number;
}

/** What `POST /api/apps/{domain}/terminal` and `POST /api/server/terminal` answer. */
export function ticketBody(options: TicketOptions = {}): Record<string, unknown> {
  const { domain = "shop.example.com", session = "s-1", resume = false, idleSeconds = 600, maxSeconds = 7200 } = options;
  return {
    ticket: `ticket-${session}-${resume ? "again" : "first"}`,
    expires_in: 20,
    path: "/ws/terminal",
    session,
    resume,
    target:
      domain === null
        ? { kind: "server", domain: null, service: null, label: "The server", account: options.account ?? "root" }
        : { kind: "app", domain, service: null, label: domain, account: options.account ?? "noust-app-shop" },
    idle_seconds: idleSeconds,
    max_seconds: maxSeconds,
    recorded: true,
  };
}

/** The `ready` frame a server sends first. */
export function readyFrame(options: { session?: string; resumed?: boolean; lost?: boolean; endsIn?: number; domain?: string | null } = {}): Record<string, unknown> {
  const { session = "s-1", resumed = false, lost = false, endsIn = 7200, domain = "shop.example.com" } = options;
  return {
    type: "ready",
    session,
    resumed,
    lost,
    target:
      domain === null
        ? { kind: "server", domain: null, service: null, label: "The server", account: "root" }
        : { kind: "app", domain, service: null, label: domain, account: "noust-app-shop" },
    actor: "ada",
    rows: 24,
    cols: 80,
    started_at: 0,
    ends_at: 0,
    ends_in: endsIn,
    idle_seconds: 600,
    idle_remaining: 600,
    recorded: true,
    recording: "rec-1",
  };
}

/** The status the server reports when terminals are on and the server's shell is off. */
export const STATUS_BODY = {
  enabled: true,
  server: { allowed: false, enabled: false, reason: "The terminal on the server itself is off", how: "noust config set terminal.server.enabled true" },
  idle_seconds: 600,
  max_seconds: 7200,
  max_sessions: 8,
  max_sessions_per_user: 3,
  open_sessions: 0,
  exec_max_seconds: 1800,
  recording_max_mb: 8,
  retention_days: 90,
  ens: false,
  shells: ["bash", "sh"],
};

/** The routes a terminal page reads and writes, with the ticket call answered. */
export function terminalRoutes(ticket: () => Record<string, unknown> = () => ticketBody(), status: Record<string, unknown> = STATUS_BODY): Record<string, RouteHandler> {
  return {
    "GET /api/terminal/status": () => json(200, status),
    "POST /api/apps/shop.example.com/terminal": () => json(200, ticket()),
    "POST /api/server/terminal": () => json(200, ticket()),
  };
}

export interface Wiring {
  deps: Dependencies;
  sockets: FakeTerminalSocket[];
  /** Moves the clock the connection reads. */
  advance: (ms: number) => void;
  /** The server the console says it is on. */
  setNode: (node: string | null) => void;
}

/** The connection's dependencies, with sockets that are recorded and a clock a test moves. */
export function wire(): Wiring {
  const sockets: FakeTerminalSocket[] = [];
  let clockNow = 1_000_000;
  let node: string | null = null;
  return {
    sockets,
    advance: (ms) => {
      clockNow += ms;
    },
    setNode: (next) => {
      node = next;
    },
    deps: {
      node: () => node,
      platformTicket: () => Promise.resolve("platform-ticket"),
      address: socketUrl,
      nodePath: nodeSocketPath,
      connect: (url) => {
        const made = new FakeTerminalSocket(url);
        sockets.push(made);
        return made;
      },
      now: () => clockNow,
    },
  };
}
