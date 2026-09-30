import { describe, expect, it } from "vitest";

import { databaseProtection, newestDumps, policyProtection } from "./protection";
import { DATABASES, POLICY, dump } from "./testFixtures";

describe("whether a database is backed up", () => {
  it("reads the policy: none, paused, failing, not run yet, or working", () => {
    expect(policyProtection(undefined)).toBe("unprotected");
    expect(policyProtection({ ...POLICY, configured: false })).toBe("unprotected");
    expect(policyProtection({ ...POLICY, enabled: false })).toBe("paused");
    expect(policyProtection({ ...POLICY, last_status: "failed" })).toBe("failed");
    expect(policyProtection({ ...POLICY, last_status: null })).toBe("scheduled");
    expect(policyProtection(POLICY)).toBe("protected");
  });

  it("says a database the engine no longer has is missing, whatever its policy says", () => {
    const [first] = DATABASES;
    if (!first) throw new Error("no database");
    const missing = { ...first, missing: true };
    expect(databaseProtection(missing, POLICY).protection).toBe("missing");
  });

  it("takes each database's newest dump", () => {
    const newest = newestDumps([dump("a", { created: "2026-09-20T00:00:00Z" }), dump("b", { created: "2026-09-29T00:00:00Z" })]);
    expect(newest.get("postgresql/example_production")?.name).toBe("b");
  });
});
