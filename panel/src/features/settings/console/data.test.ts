import { describe, expect, it } from "vitest";

import { EMPTY_FORM, accountsWithoutSecondFactor, bodyOf, formProblem } from "./data";

const account = (username: string, extra: Record<string, unknown> = {}) => ({ username, status: "active", locked_until: null, mfa_enabled: true, ...extra });

describe("the accounts that stop the console from being published", () => {
  it("are those that can sign in without a second factor, by name", () => {
    expect(accountsWithoutSecondFactor([account("zoe", { mfa_enabled: false }), account("ana", { mfa_enabled: false }), account("lee")])).toEqual(["ana", "zoe"]);
  });

  it("leave out accounts that cannot sign in: disabled, or invited and not yet joined", () => {
    expect(accountsWithoutSecondFactor([account("a", { status: "disabled", mfa_enabled: false }), account("b", { status: "invited", mfa_enabled: false })])).toEqual([]);
  });

  it("count a locked account whose lock has run out, and not one that is still locked", () => {
    const now = 1_000_000;
    expect(accountsWithoutSecondFactor([account("a", { status: "locked", locked_until: 999, mfa_enabled: false })], now)).toEqual(["a"]);
    expect(accountsWithoutSecondFactor([account("b", { status: "locked", locked_until: 5_000, mfa_enabled: false })], now)).toEqual([]);
  });

  it("are nobody when every account has one, or when there are none", () => {
    expect(accountsWithoutSecondFactor([account("a")])).toEqual([]);
    expect(accountsWithoutSecondFactor([])).toEqual([]);
  });
});

describe("the publish form", () => {
  it("sends the name as typed and the allow-list as valid entries", () => {
    expect(bodyOf({ domain: " console.example.com ", allow: "203.0.113.0/24, nope\n198.51.100.7", skipDns: true, forceEns: false })).toEqual({
      domain: "console.example.com",
      allow_ips: ["203.0.113.0/24", "198.51.100.7"],
      force_ens: false,
      skip_dns_check: true,
    });
  });

  it("needs a name first, then a valid allow-list", () => {
    expect(formProblem(EMPTY_FORM, true)).toEqual({ problem: "domain", entries: [] });
    expect(formProblem({ ...EMPTY_FORM, domain: "c.example.com", allow: "203.0.113.7 whoever" }, true)).toEqual({ problem: "allow", entries: ["whoever"] });
    expect(formProblem({ ...EMPTY_FORM, domain: "c.example.com" }, true)).toBeNull();
  });

  it("needs an allow-list to force the ENS profile, which only matters where the profile blocks", () => {
    expect(formProblem({ ...EMPTY_FORM, domain: "c.example.com", forceEns: true }, false)).toEqual({ problem: "ens", entries: [] });
    expect(formProblem({ ...EMPTY_FORM, domain: "c.example.com", forceEns: true, allow: "203.0.113.7" }, false)).toBeNull();
    expect(formProblem({ ...EMPTY_FORM, domain: "c.example.com", forceEns: true }, true)).toBeNull();
  });
});
