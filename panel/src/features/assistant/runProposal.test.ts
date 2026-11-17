import { afterEach, describe, expect, it } from "vitest";

import { installNodeSource, resetNodeScope } from "../../api/nodeScope";
import { fakeBackend, json } from "../../test/fakes";
import { asProposal } from "./model";
import type { Proposal } from "./model";
import { ProposalNotSent, carryOut } from "./runProposal";

function proposalOf(call: { path: string; method?: string }, over: Record<string, unknown> = {}): Proposal {
  const made = asProposal({
    id: "p1",
    turn: "t1",
    tool: "call_operation",
    klass: "ask",
    state: "waiting",
    fingerprint: "f".repeat(64),
    server: "",
    call: { method: call.method ?? "POST", path: call.path, query: [], body: null, headers: { "X-Noust-Request-Id": "ap-1" } },
    ...over,
  });
  if (made === null) throw new Error("not a proposal");
  return made;
}

afterEach(() => {
  resetNodeScope();
});

describe("carrying out a proposal", () => {
  it("makes the call the proposal holds, as written, with the identifier that proves it", async () => {
    const backend = fakeBackend({ "POST /api/apps/shop.example.com/restart": () => json(200, { ok: true }) });

    const outcome = await carryOut(proposalOf({ path: "/api/apps/shop.example.com/restart" }));

    expect(outcome).toEqual({ fingerprint: "f".repeat(64), status: 200 });
    expect(backend.calls.map((call) => `${call.method} ${call.path}`)).toEqual(["POST /api/apps/shop.example.com/restart"]);
    expect(backend.calls[0]?.headers.get("X-Noust-Request-Id")).toBe("ap-1");
  });

  it.each([
    ["a dot segment", "/api/teams/../notifications/channels/alerts"],
    ["a single dot", "/api/teams/./x/restart"],
    ["an encoded dot segment", "/api/teams/%2e%2E/notifications/channels/alerts"],
    ["a backslash", "/api/apps\\shop.example.com\\restart"],
    ["another host", "//evil.example.com/api/apps/x/restart"],
    ["a space", "/api/apps/a b/restart"],
    ["a fragment", "/api/apps/x/restart#y"],
  ])("refuses to send a path that holds %s: the browser would send another call than the card shows", async (_what, path) => {
    const backend = fakeBackend({});

    await expect(carryOut(proposalOf({ path }))).rejects.toBeInstanceOf(ProposalNotSent);

    expect(backend.calls).toEqual([]);
  });

  it("says what it refused to do, and what the person can do instead", async () => {
    fakeBackend({});

    const refused: unknown = await carryOut(proposalOf({ path: "/api/teams/../notifications/channels/alerts" })).catch((error: unknown) => error);

    expect(refused).toBeInstanceOf(ProposalNotSent);
    expect((refused as ProposalNotSent).message).toContain("/api/teams/../notifications/channels/alerts");
    expect((refused as ProposalNotSent).hint).not.toBe("");
  });

  it("sends a call for this server to this server, whichever server is selected by now", async () => {
    // The proposal was made while the person looked at this server; they switched to web-2 before approving.
    installNodeSource(() => "web-2");
    const backend = fakeBackend({ "POST /api/apps/shop.example.com/restart": () => json(200, { ok: true }) });

    await carryOut(proposalOf({ path: "/api/apps/shop.example.com/restart" }));

    expect(backend.calls.map((call) => call.path)).toEqual(["/api/apps/shop.example.com/restart"]);
  });

  it("sends a call for a server to that server, whichever server is selected by now", async () => {
    installNodeSource(() => "web-3");
    const backend = fakeBackend({ "POST /api/nodes/web-2/api/apps/shop.example.com/restart": () => json(200, { ok: true }) });

    await carryOut(proposalOf({ path: "/api/nodes/web-2/api/apps/shop.example.com/restart" }, { server: "web-2" }));

    expect(backend.calls.map((call) => call.path)).toEqual(["/api/nodes/web-2/api/apps/shop.example.com/restart"]);
  });

  it("refuses a call whose path goes to another server than the card says", async () => {
    const backend = fakeBackend({});

    await expect(carryOut(proposalOf({ path: "/api/nodes/web-3/api/apps/x/restart" }, { server: "web-2" }))).rejects.toBeInstanceOf(ProposalNotSent);
    await expect(carryOut(proposalOf({ path: "/api/nodes/web-2/api/apps/x/restart" }, { server: "" }))).rejects.toBeInstanceOf(ProposalNotSent);
    await expect(carryOut(proposalOf({ path: "/api/apps/x/restart" }, { server: "web-2" }))).rejects.toBeInstanceOf(ProposalNotSent);

    expect(backend.calls).toEqual([]);
  });
});
