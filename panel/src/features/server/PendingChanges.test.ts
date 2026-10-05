import { describe, expect, it } from "vitest";

import { countdown, proofOf } from "./PendingChanges";
import { pendingChange, pendingChangeFromOlderNode } from "./testing";

describe("countdown", () => {
  it("counts minutes and seconds down to the change's end, never below zero", () => {
    const now = 1_000_000_000_000;
    expect(countdown(now / 1000 + 102, now)).toBe("1:42");
    expect(countdown(now / 1000 + 9, now)).toBe("0:09");
    expect(countdown(now / 1000 - 5, now)).toBe("0:00");
  });
});

describe("proofOf", () => {
  it("is waiting while the server reads the history and sees no new login", () => {
    expect(proofOf(pendingChange())).toEqual({ kind: "waiting" });
  });

  it("is seen once the login is on record", () => {
    const login = { user: "alex", source: "203.0.113.5", at: 1_800_000_000 };
    expect(proofOf(pendingChange(Date.now(), { proof_seen: true, proof_login: login }))).toEqual({ kind: "seen", ...login });
  });

  it("is unreadable only when the server says the history cannot be read", () => {
    expect(proofOf(pendingChange(Date.now(), { proof_readable: false, proof_error: "no journal" }))).toEqual({
      kind: "unreadable",
      error: "no journal",
    });
  });

  it("is unknown, not unreadable, when the server leaves the proof fields out", () => {
    // A node on 3.3.0 does not send them, and a newer central proxies it as it is.
    expect(proofOf(pendingChangeFromOlderNode())).toEqual({ kind: "unknown" });
  });
});
