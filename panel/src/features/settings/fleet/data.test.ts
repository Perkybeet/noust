import { afterEach, describe, expect, it, vi } from "vitest";

import { applyToFleet, pendingCount } from "./data";
import type { Cell, FleetStatus } from "./data";

const cell = (over: Partial<Cell>): Cell => ({ node: "fra-2", origin: "fleet", sync: "in_sync", ...over });

describe("what waits to be applied", () => {
  const status = (syncs: Cell["sync"][]): FleetStatus => ({
    generated_at: "2026-10-29T10:00:00Z",
    partial: false,
    servers: [],
    released: [],
    settings: [{ resource: "variable", name: "A", version: 1, change: "c1", changed_by: "marta", changed_at: "2026-10-29T10:00:00Z", exceptions: [], servers: syncs.map((sync, index) => cell({ node: `s${String(index)}`, sync })) }],
  });
  it("counts the servers that are not what the fleet says, and not the ones that are well or out of reach", () => {
    expect(pendingCount(status(["in_sync", "pending", "diverged", "unreachable", "not_applicable"]))).toBe(2);
    expect(pendingCount(undefined)).toBe(0);
  });
});

describe("asking to apply", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function bodyOfApply(options: Parameters<typeof applyToFleet>[0]): Promise<Record<string, unknown>> {
    return new Promise((resolve) => {
      vi.stubGlobal(
        "fetch",
        vi.fn((_url: string, init: RequestInit) => {
          resolve(JSON.parse(init.body as string) as Record<string, unknown>);
          return Promise.resolve(new Response(JSON.stringify({ plan: { nodes: [], summary: { run: 0, skipped: 0 } }, job: null }), { status: 200, headers: { "content-type": "application/json" } }));
        }),
      );
      void applyToFleet(options);
    });
  }

  it("asks for everything with nothing but the plan, as it always did", async () => {
    expect(await bodyOfApply({ plan: true })).toEqual({ plan: true, adopt: false, restore_all: false });
  });

  it("limits what is applied to some servers and to some settings, only when it is asked to", async () => {
    expect(await bodyOfApply({ plan: false, nodes: ["web-02"], only: ["channel/slack-deploys"] })).toEqual({
      plan: false,
      adopt: false,
      restore_all: false,
      nodes: ["web-02"],
      only: ["channel/slack-deploys"],
    });
  });

  it("does not send an empty list as a limit: an empty `nodes` is every server", async () => {
    expect(await bodyOfApply({ plan: true, nodes: [], only: [] })).toEqual({ plan: true, adopt: false, restore_all: false });
  });
});
