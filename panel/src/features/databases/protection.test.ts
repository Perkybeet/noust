import { describe, expect, it } from "vitest";

import { coveringPolicy, databaseProtection, effectivePolicy, newestDumps, policyProtection } from "./protection";
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

describe("which policy backs a database up", () => {
  const everything = { ...POLICY, database: "*", scope: "all", exclusions: { databases: ["scratch"] } };
  const own = { ...POLICY, database: "orders" };
  const map = (...policies: (typeof POLICY)[]) => new Map(policies.map((policy) => [`${policy.engine}/${policy.database}`, policy]));

  it("is its own policy when it has one, even when the engine has one for every database", () => {
    expect(coveringPolicy(map(everything, own), "postgresql", "orders")).toBe(own);
  });

  it("is the policy for every database when it has none of its own and is not left out", () => {
    expect(coveringPolicy(map(everything), "postgresql", "orders")).toBe(everything);
    expect(coveringPolicy(map(everything), "postgresql", "created_next_month")).toBe(everything);
  });

  it("is nothing for a database the policy for every database leaves out", () => {
    expect(coveringPolicy(map(everything), "postgresql", "scratch")).toBeUndefined();
  });

  it("is nothing for an engine that has no policy for every database, whatever another engine has", () => {
    expect(coveringPolicy(map(everything), "mysql", "shop_wp")).toBeUndefined();
  });

  it("ignores an all-databases policy that was never configured", () => {
    expect(effectivePolicy(undefined, { ...everything, configured: false }, "orders")).toBeUndefined();
  });
});
