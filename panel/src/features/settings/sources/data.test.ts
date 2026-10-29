import { describe, expect, it } from "vitest";

import { repositoriesLeased, soonest, stateView } from "./data";

const lease = (repositories: string[], expires_in: number | null) => ({ key: "k", state: "leased", installation_id: 1, repositories, expires_in });

describe("how a server's way to its repositories reads", () => {
  it("is plain, with no colour, for a server that has a token", () => {
    expect(stateView("leased")).toEqual({ label: "connections.state.leased", pill: null });
  });
  it("is grey for what is left alone or has nothing to do, amber where the server's own limit stops it, red where it failed", () => {
    expect(stateView("independent").pill).toBe("stopped");
    expect(stateView("no_app").pill).toBe("stopped");
    expect(stateView("ceiling").pill).toBe("warning");
    expect(stateView("unreachable").pill).toBe("failed");
    expect(stateView("failed").pill).toBe("failed");
  });
  it("says a state a newer central sends and this console does not know as not read yet, never as a problem", () => {
    expect(stateView("something-new")).toEqual({ label: "connections.state.unknown", pill: "stopped" });
  });
});

describe("what a server's leases come to", () => {
  it("is the soonest end, and the repositories counted once each", () => {
    const node = { leases: [lease(["acme/web"], 3000), lease(["acme/web", "acme/api"], 1200)] };
    expect(soonest(node)).toBe(1200);
    expect(repositoriesLeased(node)).toBe(2);
  });
  it("is nothing for a server with no lease", () => {
    expect(soonest({ leases: [] })).toBeNull();
    expect(repositoriesLeased({ leases: [] })).toBe(0);
  });
});
