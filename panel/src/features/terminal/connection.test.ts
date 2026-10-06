import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WS_CLOSE_UNAUTHORIZED } from "../../realtime/sockets";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { FakeBackend } from "../../test/fakes";
import { RECONNECT_WINDOW_MS, TerminalConnection } from "./connection";
import type { TerminalTarget } from "./connection";
import { readyFrame, terminalRoutes, wire } from "./testing";
import type { Wiring } from "./testing";

const APP: TerminalTarget = { kind: "app", domain: "shop.example.com" };

async function settle(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

function setup(target: TerminalTarget = APP, routes = terminalRoutes()): { connection: TerminalConnection; wiring: Wiring; backend: FakeBackend } {
  const backend = fakeBackend({ ...signedInRoutes(), ...routes });
  const wiring = wire();
  return { connection: new TerminalConnection(target, wiring.deps), wiring, backend };
}

/** Opens a session and brings it to `open`, the way a person pressing the button does. */
async function opened(target: TerminalTarget = APP): Promise<ReturnType<typeof setup>> {
  const made = setup(target);
  made.connection.open();
  await settle();
  const socket = made.wiring.sockets[0];
  if (socket === undefined) throw new Error("no socket");
  socket.open();
  socket.frame(readyFrame({ domain: target.kind === "app" ? target.domain : null }));
  return made;
}

describe("TerminalConnection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  describe("opening", () => {
    it("asks for a ticket, then redeems it on the socket with both credentials", async () => {
      const { connection, wiring, backend } = setup();
      connection.open({ shell: "bash", service: "web" });
      expect(connection.getState().phase).toBe("opening");
      await settle();

      expect(backend.callsTo("POST /api/apps/shop.example.com/terminal")[0]?.body).toEqual({ rows: 24, cols: 80, shell: "bash", service: "web" });
      const socket = wiring.sockets[0];
      expect(socket).toBeDefined();
      const url = new URL(socket?.url ?? "");
      expect(url.pathname).toBe("/ws/terminal");
      // The console's own single-use ticket authenticates the handshake; the terminal's names the shell.
      expect(url.searchParams.get("ticket")).toBe("platform-ticket");
      expect(url.searchParams.get("terminal_ticket")).toBe("ticket-s-1-first");
    });

    it("uses the server's address for the server's terminal", async () => {
      const { connection, wiring, backend } = setup({ kind: "server" });
      connection.open();
      await settle();
      expect(backend.callsTo("POST /api/server/terminal")).toHaveLength(1);
      expect(backend.callsTo("POST /api/apps/shop.example.com/terminal")).toHaveLength(0);
      expect(wiring.sockets).toHaveLength(1);
    });

    it("reaches a node's terminal through the central's relay", async () => {
      const { connection, wiring } = setup();
      wiring.setNode("web-2");
      connection.open();
      await settle();
      expect(new URL(wiring.sockets[0]?.url ?? "").pathname).toBe("/ws/nodes/web-2/terminal");
    });

    it("is open once the server says who, where and for how long", async () => {
      const { connection } = await opened();
      const state = connection.getState();
      expect(state.phase).toBe("open");
      expect(state.info).toMatchObject({
        session: "s-1",
        actor: "ada",
        idleSeconds: 600,
        recorded: true,
        target: { label: "shop.example.com", account: "noust-app-shop" },
      });
      // Counted from the server's own "ends in" (7200 s), on this browser's clock (1,000,000 ms).
      expect(state.info?.endsAt).toBe(8_200_000);
    });

    it("tells the shell the screen's real size as soon as it is ready", async () => {
      const { connection, wiring } = await opened();
      connection.getSource().terminal.resize(120, 40);
      wiring.sockets[0]?.frame(readyFrame({ resumed: true }));
      const resizes = wiring.sockets[0]?.frames().filter((frame) => frame["type"] === "resize");
      expect(resizes?.at(-1)).toEqual({ type: "resize", cols: 120, rows: 40 });
    });

    it("says why it did not open, in the server's own words", async () => {
      const { connection } = setup(APP, {
        ...terminalRoutes(),
        "POST /api/apps/shop.example.com/terminal": () =>
          problem(403, "terminal_disabled", "Terminals are turned off on this server", { hint: "noust config set terminal.enabled true" }),
      });
      connection.open();
      await settle();
      const state = connection.getState();
      expect(state.phase).toBe("failed");
      expect(state.failure?.code).toBe("terminal_disabled");
      expect(state.failure?.error).toMatchObject({ detail: "Terminals are turned off on this server" });
    });

    it("fails when the socket is refused, with the refusal's sentence", async () => {
      const { connection, wiring } = setup();
      connection.open();
      await settle();
      const socket = wiring.sockets[0];
      socket?.frame({ type: "error", code: "limit", message: "Too many terminals are open", details: "End one first" });
      const state = connection.getState();
      expect(state.phase).toBe("failed");
      expect(state.failure).toMatchObject({ code: "limit", error: { detail: "Too many terminals are open", hint: "End one first" } });
    });

    it("fails, and says so, when the socket closes before it opened", async () => {
      const { connection, wiring } = setup();
      connection.open();
      await settle();
      wiring.sockets[0]?.drop(1006);
      expect(connection.getState().phase).toBe("failed");
    });
  });

  describe("a session", () => {
    it("draws what the shell prints", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.frame({ type: "output", data: "hello\r\n$ " });
      expect(connection.getSource().terminal.screen.text()).toContain("hello");
    });

    it("tells the screen it changed", async () => {
      const { connection, wiring } = await opened();
      const changed = vi.fn();
      connection.getSource().subscribe(changed);
      wiring.sockets[0]?.frame({ type: "output", data: "x" });
      expect(changed).toHaveBeenCalled();
    });

    it("sends what is typed, and answers the shell's questions as if typed", async () => {
      const { connection, wiring } = await opened();
      connection.send("ls\r");
      // "Where is the cursor?": a program asks and the terminal answers on the same line.
      wiring.sockets[0]?.frame({ type: "output", data: "\u001b[6n" });
      expect(wiring.sockets[0]?.typed()).toBe("ls\r\u001b[1;1R");
    });

    it("sends the new size when the screen changes", async () => {
      const { connection, wiring } = await opened();
      connection.resize(100, 30);
      expect(wiring.sockets[0]?.frames().at(-1)).toEqual({ type: "resize", cols: 100, rows: 30 });
    });

    it("does not send anything before the socket is open", async () => {
      const { connection, wiring } = setup();
      connection.open();
      await settle();
      connection.send("ls\r");
      expect(wiring.sockets[0]?.sent).toEqual([]);
    });

    it("warns that a limit is near, and the warning ends when something is typed", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.frame({ type: "notice", kind: "idle", seconds: 55 });
      expect(connection.getState().notice).toMatchObject({ kind: "idle" });
      connection.send("x");
      expect(connection.getState().notice).toBeNull();

      wiring.sockets[0]?.frame({ type: "notice", kind: "limit", seconds: 30 });
      connection.send("y");
      // Typing does not move the absolute limit.
      expect(connection.getState().notice).toMatchObject({ kind: "limit" });
    });

    it("ends with why, and closes the socket", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.frame({ type: "ended", reason: "exited", exit_code: 3 });
      expect(connection.getState()).toMatchObject({ phase: "ended", ended: { reason: "exited", exitCode: 3 } });
      expect(wiring.sockets[0]?.closedWith).toBe(1000);
    });

    it("asks the server to end the session, which answers with why", async () => {
      const { connection, wiring } = await opened();
      connection.end();
      expect(wiring.sockets[0]?.frames().at(-1)).toEqual({ type: "close" });
      expect(connection.getState().phase).toBe("open");
      wiring.sockets[0]?.frame({ type: "ended", reason: "closed", exit_code: null });
      expect(connection.getState().ended?.reason).toBe("closed");
    });

    it("ends at once when there is no session to ask", () => {
      const { connection } = setup();
      connection.open();
      connection.end();
      expect(connection.getState()).toMatchObject({ phase: "ended", ended: { reason: "closed" } });
    });

    it("keeps the session on a refusal of what was typed, and on a window taking it over", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.frame({ type: "error", code: "input", message: "Too much at once" });
      expect(connection.getState().phase).toBe("open");
    });

    it("ends a session whose sign-in is gone", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.drop(WS_CLOSE_UNAUTHORIZED);
      expect(connection.getState()).toMatchObject({ phase: "ended", ended: { reason: "revoked" } });
    });

    it("starts again with an empty screen", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.frame({ type: "output", data: "old" });
      const before = connection.getSource();
      connection.restart();
      expect(connection.getState().phase).toBe("idle");
      expect(connection.getSource()).not.toBe(before);
      expect(connection.getSource().terminal.screen.text().trim()).toBe("");
    });

    it("ignores the answer to a ticket asked for before a restart", async () => {
      const { connection, wiring } = setup();
      connection.open();
      connection.restart();
      await settle();
      expect(wiring.sockets).toHaveLength(0);
      expect(connection.getState().phase).toBe("idle");
    });
  });

  describe("a dropped connection", () => {
    it("is not the end of the shell: it asks to resume the same session", async () => {
      const { connection, wiring, backend } = await opened();
      wiring.sockets[0]?.drop(1006);
      expect(connection.getState().phase).toBe("reconnecting");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(backend.callsTo("POST /api/apps/shop.example.com/terminal")[1]?.body).toMatchObject({ resume: "s-1" });
      expect(wiring.sockets).toHaveLength(2);

      wiring.sockets[1]?.open();
      wiring.sockets[1]?.frame(readyFrame({ resumed: true, lost: true }));
      expect(connection.getState()).toMatchObject({ phase: "open", resumed: true, lost: true });
    });

    it("keeps the screen while it is away, and delivers what came meanwhile", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.frame({ type: "output", data: "before" });
      wiring.sockets[0]?.drop(1006);
      expect(connection.getSource().terminal.screen.text()).toContain("before");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      wiring.sockets[1]?.open();
      wiring.sockets[1]?.frame(readyFrame({ resumed: true }));
      wiring.sockets[1]?.frame({ type: "output", data: " and after" });
      expect(connection.getSource().terminal.screen.text()).toContain("before and after");
    });

    it("gives up when it has been away longer than the server keeps the shell", async () => {
      const { connection, wiring, backend } = await opened();
      backend.on("POST /api/apps/shop.example.com/terminal", () => json(503, { error: "unavailable", detail: "down" }));
      wiring.sockets[0]?.drop(1006);
      wiring.advance(RECONNECT_WINDOW_MS + 1_000);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      // The retry that was due failed, and found the window closed.
      expect(connection.getState()).toMatchObject({ phase: "ended", ended: { reason: "disconnected" } });
    });

    it("ends when the server no longer has the session", async () => {
      const { connection, wiring, backend } = await opened();
      backend.on("POST /api/apps/shop.example.com/terminal", () => problem(404, "terminal_gone", "That terminal is no longer open"));
      wiring.sockets[0]?.drop(1006);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(connection.getState()).toMatchObject({ phase: "ended", ended: { reason: "disconnected" } });
    });

    it("tries again, slower, while the network is down", async () => {
      const { connection, wiring, backend } = await opened();
      backend.on("POST /api/apps/shop.example.com/terminal", () => json(503, { error: "unavailable", detail: "down" }));
      wiring.sockets[0]?.drop(1006);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(connection.getState().phase).toBe("reconnecting");
      const asked = backend.callsTo("POST /api/apps/shop.example.com/terminal").length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });
      expect(backend.callsTo("POST /api/apps/shop.example.com/terminal").length).toBeGreaterThan(asked);
    });

    it("is not resumed after the person ended it", async () => {
      const { connection, wiring } = await opened();
      wiring.sockets[0]?.frame({ type: "ended", reason: "closed", exit_code: null });
      wiring.sockets[0]?.drop(1000);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(wiring.sockets).toHaveLength(1);
      expect(connection.getState().phase).toBe("ended");
    });
  });

  it("leaves the shell to the server's minute when the page goes away", async () => {
    const { connection, wiring } = await opened();
    connection.dispose();
    expect(wiring.sockets[0]?.closedWith).toBe(1000);
    // No close frame: the shell is not ended, only the connection.
    expect(wiring.sockets[0]?.frames().some((frame) => frame["type"] === "close")).toBe(false);
  });
});
