import { describe, expect, it } from "vitest";

import { accountTypeWords, hooksState, organizationProblem, parseCallback, repositorySelectionWords, splitFullName } from "./github";

describe("parseCallback", () => {
  it("reads the App's creation, keeping the code and state exactly as sent", () => {
    expect(parseCallback("?code=abc&state=1e5")).toEqual({ kind: "conversion", code: "abc", state: "1e5" });
    expect(parseCallback("code=00123&state=s")).toEqual({ kind: "conversion", code: "00123", state: "s" });
  });

  it("reads an installation and what GitHub did", () => {
    expect(parseCallback("?installation_id=7001&setup_action=install")).toEqual({ kind: "installation", installationId: 7001, setupAction: "install" });
    expect(parseCallback("?installation_id=7001")).toEqual({ kind: "installation", installationId: 7001, setupAction: null });
  });

  it("tells a pending request from an address with nothing in it", () => {
    expect(parseCallback("?setup_action=request")).toEqual({ kind: "requested" });
    expect(parseCallback("")).toEqual({ kind: "invalid" });
    expect(parseCallback("?code=abc")).toEqual({ kind: "invalid" });
    expect(parseCallback("?installation_id=abc")).toEqual({ kind: "invalid" });
  });
});

describe("the words for an installation", () => {
  it("names the account kind and the repositories it covers", () => {
    expect(accountTypeWords("Organization")).toBe("Organization");
    expect(accountTypeWords("User")).toBe("Personal account");
    expect(accountTypeWords(null)).toBe("Account");
    expect(repositorySelectionWords("all")).toBe("All repositories");
    expect(repositorySelectionWords("selected")).toBe("Selected repositories");
    expect(repositorySelectionWords(undefined)).toBe("Repositories not reported");
  });
});

describe("hooksState", () => {
  it("follows the public address and GitHub's webhook", () => {
    expect(hooksState({ configured: true, hooks_url: null, hooks_active: false })).toBe("unexposed");
    expect(hooksState({ configured: false, hooks_url: "", hooks_active: false })).toBe("unexposed");
    expect(hooksState({ configured: false, hooks_url: "https://h/hooks/github", hooks_active: false })).toBe("ready");
    expect(hooksState({ configured: true, hooks_url: "https://h/hooks/github", hooks_active: false })).toBe("inactive");
    expect(hooksState({ configured: true, hooks_url: "https://h/hooks/github", hooks_active: true })).toBe("active");
  });
});

describe("organizationProblem", () => {
  it("takes what GitHub takes, and empty for the personal account", () => {
    expect(organizationProblem("")).toBeNull();
    expect(organizationProblem("acme")).toBeNull();
    expect(organizationProblem("acme-labs-2")).toBeNull();
    expect(organizationProblem("acme corp")).not.toBeNull();
    expect(organizationProblem("-acme")).not.toBeNull();
    expect(organizationProblem("acme--labs")).not.toBeNull();
    expect(organizationProblem("a".repeat(40))).not.toBeNull();
  });
});

describe("splitFullName", () => {
  it("splits owner/repo and nothing else", () => {
    expect(splitFullName("acme/shop")).toEqual({ owner: "acme", repo: "shop" });
    expect(splitFullName("acme")).toBeNull();
    expect(splitFullName("acme/shop/x")).toBeNull();
  });
});
