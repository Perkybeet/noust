import { describe, expect, it } from "vitest";

import { applyLive, asProposal, asSentence, asStep, asTurn, pageBody, startLive } from "./model";
import { pageForPath } from "./pageContext";

const SENTENCE = { code: "assistant.step.deployment_log", params: { id: 14 }, text: "Read the log of deployment 14", hint: null };

function proposalJson(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "p1",
    turn: "t1",
    tool: "restart_application",
    klass: "ask",
    state: "waiting",
    title: SENTENCE,
    effect: "Restart an application",
    why: null,
    permission: "apps.operate",
    environment: "production",
    server: "",
    facts: [{ name: "domain", value: "shop.example.com", foreign: null }],
    plan: null,
    needs: { elevation: false, second_person: false },
    call: { method: "POST", path: "/api/apps/shop.example.com/restart", query: [["force", "1"]], body: null, headers: { "X-Noust-Request-Id": "req-1" } },
    fingerprint: "abc",
    created_at: "2026-11-13T10:00:00+00:00",
    decided_at: null,
    decided_by: null,
    status: null,
    result: null,
    output: null,
    approval: null,
    job_id: null,
    verified: true,
    ...over,
  };
}

describe("reading what the server says", () => {
  it("keeps a sentence with its code and values and leaves out what is not one", () => {
    expect(asSentence(SENTENCE)).toEqual({ code: "assistant.step.deployment_log", params: { id: 14 }, text: "Read the log of deployment 14", hint: null });
    expect(asSentence({ text: "no code" })).toBeNull();
    expect(asSentence("text")).toBeNull();
    expect(asSentence({ code: "x", params: { nested: { a: 1 }, ok: "yes" }, text: "t" })?.params).toEqual({ ok: "yes" });
  });

  it("reads a step, and takes a state it does not know for done rather than inventing one", () => {
    expect(asStep({ id: "s1", tool: "list_deployments", state: "mystery", what: SENTENCE, outside: true, size: { unit: "lines", count: 120 }, masked: 2 })).toMatchObject({ state: "done", outside: true, masked: 2, size: { unit: "lines", count: 120 } });
    expect(asStep({ tool: "no id" })).toBeNull();
  });

  it("reads a proposal as the exact call, and a class it does not know as the one that asks the person", () => {
    const proposal = asProposal(proposalJson({ klass: "something-new" }));
    expect(proposal?.klass).toBe("ask");
    expect(proposal?.call).toEqual({ method: "POST", path: "/api/apps/shop.example.com/restart", query: [["force", "1"]], body: null, headers: { "X-Noust-Request-Id": "req-1" } });
    expect(proposal?.facts).toEqual([{ name: "domain", value: "shop.example.com", foreign: null }]);
  });

  it("keeps where a value that is not the person's was found", () => {
    const proposal = asProposal(proposalJson({ facts: [{ name: "domain", value: "cdn.example.org", foreign: { kind: "host", source: "log:14", line: 41, label: SENTENCE } }] }));
    expect(proposal?.facts[0]?.foreign).toMatchObject({ kind: "host", source: "log:14", line: 41 });
  });

  it("refuses a proposal that has no call: there is nothing to approve", () => {
    expect(asProposal({ id: "p1" })).toBeNull();
  });

  it("reads a turn with its answer cut into the text and the lines the system said", () => {
    const turn = asTurn({
      id: "t1",
      seq: 1,
      state: "done",
      question: "Why?",
      model: "tiny-model",
      location: "machine",
      started_at: "2026-11-13T10:00:00+00:00",
      ended_at: "2026-11-13T10:00:05+00:00",
      steps: [{ id: "s1", tool: "x", state: "done" }],
      text: "It failed.",
      parts: [{ type: "literal", source: { id: "S1", kind: "deployment_log", label: SENTENCE }, lines: [{ n: 41, text: "Error: EADDRINUSE" }] }, { type: "text", text: "It failed." }, { type: "unknown" }],
      proposals: [proposalJson()],
      not_offered: [{ title: SENTENCE, why: SENTENCE, floor: true }],
      warnings: [SENTENCE],
      stop: null,
      error: null,
      usage: { steps: 2, tokens: 1200, spend: 0.01, conversation: { tokens: 1200, spend: 0.01, steps: 2 }, currency: "EUR", actions_last_hour: 0 },
    });
    expect(turn?.state).toBe("done");
    expect(turn?.parts.map((part) => part.type)).toEqual(["literal", "text"]);
    expect(turn?.proposals).toHaveLength(1);
    expect(turn?.notOffered[0]?.floor).toBe(true);
    expect(turn?.usage?.conversation?.tokens).toBe(1200);
    expect(asTurn({ question: "no id" })).toBeNull();
  });
});

describe("following a turn while it is written", () => {
  const live = startLive({ turn: "t1", model: "tiny-model", location: "machine", server: "" });

  it("starts from the start event and from nothing else", () => {
    expect(live?.id).toBe("t1");
    expect(startLive({})).toBeNull();
  });

  it("adds the words as they come, and what was said before a step is a draft the step puts aside", () => {
    if (live === null) throw new Error("no live turn");
    const said = applyLive(live, { type: "text", delta: "Let me look " });
    if (said === "end") throw new Error("ended");
    expect(said.draft).toBe("Let me look ");
    const stepped = applyLive(said, { type: "step", step: { id: "s1", tool: "list_deployments", state: "running", what: SENTENCE } });
    if (stepped === "end") throw new Error("ended");
    expect(stepped.draft).toBe("");
    expect(stepped.steps).toHaveLength(1);
    // The same step, finished, replaces itself: it is not read twice.
    const finished = applyLive(stepped, { type: "step", step: { id: "s1", tool: "list_deployments", state: "done", what: SENTENCE } });
    if (finished === "end") throw new Error("ended");
    expect(finished.steps).toHaveLength(1);
    expect(finished.steps[0]?.state).toBe("done");
  });

  it("collects a proposal and a warning, and says when the turn ended", () => {
    if (live === null) throw new Error("no live turn");
    const withProposal = applyLive(live, { type: "proposal", proposal: proposalJson() });
    if (withProposal === "end") throw new Error("ended");
    expect(withProposal.proposals).toHaveLength(1);
    const warned = applyLive(withProposal, { type: "warning", warning: SENTENCE });
    if (warned === "end") throw new Error("ended");
    expect(warned.warnings).toHaveLength(1);
    expect(applyLive(warned, { type: "end" })).toBe("end");
  });

  it("ignores an event of a kind it does not know", () => {
    if (live === null) throw new Error("no live turn");
    expect(applyLive(live, { type: "from-the-future" })).toBe(live);
  });
});

describe("the page the panel reads", () => {
  it("is a deployment or the diagnosis of an application, and nothing else", () => {
    expect(pageForPath("/apps/shop.example.com/deployments/14")).toEqual({ kind: "deployment", id: 14, domain: "shop.example.com" });
    expect(pageForPath("/apps/shop.example.com/diagnose")).toEqual({ kind: "diagnosis", domain: "shop.example.com" });
    expect(pageForPath("/apps/shop.example.com")).toBeNull();
    expect(pageForPath("/settings/tokens")).toBeNull();
  });

  it("is sent to the API without the domain of a deployment, which the API reads from its id, and with the server when there is one", () => {
    expect(pageBody({ kind: "deployment", id: 14, domain: "shop.example.com" }, null)).toEqual({ kind: "deployment", id: 14 });
    expect(pageBody({ kind: "diagnosis", domain: "shop.example.com" }, "web-01")).toEqual({ kind: "diagnosis", domain: "shop.example.com", server: "web-01" });
    expect(pageBody(null, "web-01")).toBeNull();
  });
});
