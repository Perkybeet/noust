import { describe, expect, it } from "vitest";

import { answerOfNode, fleetServersOf } from "./servers";

const NOW = Date.UTC(2026, 10, 12, 12, 0, 0);

describe("how a server answered the fleet's Docker", () => {
  it("is fresh when it answered whole", () => {
    expect(answerOfNode({ status: "ok" }, NOW)).toEqual({ kind: "fresh" });
  });

  it("is late with the moment of what it last gave, from when it was fetched or from how old it is", () => {
    expect(answerOfNode({ status: "stale", fetched_at: "2026-11-12T09:52:00Z" }, NOW)).toEqual({ kind: "stale", since: new Date("2026-11-12T09:52:00Z") });
    expect(answerOfNode({ status: "stale", age_seconds: 7680 }, NOW)).toEqual({ kind: "stale", since: new Date(NOW - 7_680_000) });
  });

  it("is without a connection when it did not answer, and is the version it runs when that Noust does not count Docker", () => {
    expect(answerOfNode({ status: "unreachable" }, NOW)).toEqual({ kind: "unreachable" });
    expect(answerOfNode({ status: "unsupported", version: "4.2.1" }, NOW)).toEqual({ kind: "unsupported", version: "4.2.1" });
  });

  it("names the servers of a view and marks the central", () => {
    const view = { nodes: [{ name: "central-01", local: true, status: "ok" }, { name: "vps-03", local: false, status: "unsupported", version: "4.2.1" }] } as never;
    expect(fleetServersOf(view)).toEqual([
      { name: "central-01", central: true, answer: { kind: "fresh" } },
      { name: "vps-03", answer: { kind: "unsupported", version: "4.2.1" } },
    ]);
  });
});
