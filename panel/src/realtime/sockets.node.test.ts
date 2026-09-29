import { act, renderHook } from "@testing-library/react";
import { createElement } from "react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fakeBackend, json, FakeWebSocket } from "../test/fakes";
import { ProvideNode } from "../nodes/useNode";
import { StreamSocket, useLogStream } from "./sockets";

const connect = (url: string) => new FakeWebSocket(url);

async function flush(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe("a node's WebSockets", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("go through the central's relay, with the central's ticket", async () => {
    const socket = new StreamSocket({
      path: "/ws/logs/shop.example.com",
      node: "web-2",
      getTicket: () => Promise.resolve("central-ticket"),
      connect,
      onFrame: () => undefined,
      onStatus: () => undefined,
    });
    socket.start();
    await flush();
    const url = new URL(FakeWebSocket.latest().url);
    expect(url.pathname).toBe("/ws/nodes/web-2/logs/shop.example.com");
    expect(url.searchParams.get("ticket")).toBe("central-ticket");
    socket.stop();
  });

  it("stop for good when the central says the node refused it or does not exist", async () => {
    for (const code of [4403, 4404]) {
      const statuses: string[] = [];
      const refusals: string[] = [];
      const socket = new StreamSocket({
        path: "/ws/jobs/j1",
        node: "web-2",
        getTicket: () => Promise.resolve("t"),
        connect,
        onFrame: () => undefined,
        onStatus: (status) => statuses.push(status),
        onRefused: (reason) => refusals.push(reason),
      });
      socket.start();
      await flush();
      const opened = FakeWebSocket.instances.length;
      FakeWebSocket.latest().onclose?.(new CloseEvent("close", { code, reason: "Node not found: web-2" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      expect(FakeWebSocket.instances).toHaveLength(opened);
      expect(statuses.at(-1)).toBe("closed");
      expect(refusals).toEqual(["Node not found: web-2"]);
    }
  });

  it("retry when the node did not answer: tunnels come back", async () => {
    const socket = new StreamSocket({
      path: "/ws/jobs/j1",
      node: "web-2",
      getTicket: () => Promise.resolve("t"),
      connect,
      onFrame: () => undefined,
      onStatus: () => undefined,
    });
    socket.start();
    await flush();
    const opened = FakeWebSocket.instances.length;
    FakeWebSocket.latest().drop(4502);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(FakeWebSocket.instances.length).toBeGreaterThan(opened);
    socket.stop();
  });

  it("follow the node on screen, and show the relay's refusal verbatim", async () => {
    fakeBackend({ "POST /api/auth/ws-ticket": () => json(200, { ticket: "t" }) });
    const wrapper = ({ children }: { children: ReactNode }) => createElement(ProvideNode, { node: "web-2", children });
    const { result } = renderHook(() => useLogStream("shop.example.com", { getTicket: () => Promise.resolve("t"), connect }), { wrapper });
    await flush();
    expect(new URL(FakeWebSocket.latest().url).pathname).toBe("/ws/nodes/web-2/logs/shop.example.com");
    act(() => {
      FakeWebSocket.latest().onclose?.(new CloseEvent("close", { code: 4403, reason: "Node web-2 refused this central's fleet token." }));
    });
    expect(result.current.error).toBe("Node web-2 refused this central's fleet token.");
    expect(result.current.status).toBe("closed");
  });
});
