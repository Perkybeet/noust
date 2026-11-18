import { describe, expect, it } from "vitest";

import { anyPending, pendingAcross, pendingSendsOf } from "./pendingSends";

const OWED = { destination: "offsite", reason: "connection timed out", attempts: 2, since: "2026-11-16T22:00:00+00:00", next_attempt_at: "2026-11-17T02:00:00+00:00", permanent: false };

describe("the destinations a copy is still owed to", () => {
  it("reads each entry of the listing, the soonest to be tried first", () => {
    const later = { ...OWED, destination: "bucket", next_attempt_at: "2026-11-17T09:00:00+00:00" };
    expect(pendingSendsOf({ pending_sends: [later, OWED] })).toEqual([
      { destination: "offsite", reason: "connection timed out", attempts: 2, since: "2026-11-16T22:00:00+00:00", nextAttemptAt: "2026-11-17T02:00:00+00:00", permanent: false },
      { destination: "bucket", reason: "connection timed out", attempts: 2, since: "2026-11-16T22:00:00+00:00", nextAttemptAt: "2026-11-17T09:00:00+00:00", permanent: false },
    ]);
  });

  it("holds nothing for a copy that is everywhere, or that a server before this one listed without the field", () => {
    expect(pendingSendsOf({ pending_sends: [] })).toEqual([]);
    expect(pendingSendsOf({})).toEqual([]);
    expect(pendingSendsOf({ pending_sends: null })).toEqual([]);
  });

  it("does not invent what an entry did not say: no destination is no entry, and a missing time or count is empty", () => {
    expect(pendingSendsOf({ pending_sends: [{ reason: "x" }] })).toEqual([]);
    expect(pendingSendsOf({ pending_sends: [{ destination: "offsite" }] })).toEqual([{ destination: "offsite", reason: "", attempts: 0, since: null, nextAttemptAt: null, permanent: false }]);
    expect(pendingSendsOf({ pending_sends: [{ destination: "offsite", permanent: true }] })[0]?.permanent).toBe(true);
  });

  it("sums a group of copies up by destination: the soonest try, the longest wait and the most tries", () => {
    const older = { ...OWED, since: "2026-11-15T22:00:00+00:00", next_attempt_at: "2026-11-17T05:00:00+00:00", attempts: 4, permanent: true };
    const other = { ...OWED, destination: "bucket" };
    const summed = pendingAcross([{ pending_sends: [OWED, other] }, { pending_sends: [older] }, { pending_sends: [] }]);
    expect(summed.map((send) => send.destination).sort()).toEqual(["bucket", "offsite"]);
    expect(summed.find((send) => send.destination === "offsite")).toMatchObject({ attempts: 4, since: "2026-11-15T22:00:00+00:00", nextAttemptAt: "2026-11-17T02:00:00+00:00", permanent: true });
    expect(pendingAcross([])).toEqual([]);
  });

  it("says whether any copy of a list is owed", () => {
    expect(anyPending([{ pending_sends: [] }, { pending_sends: [OWED] }])).toBe(true);
    expect(anyPending([{ pending_sends: [] }, {}])).toBe(false);
  });
});
