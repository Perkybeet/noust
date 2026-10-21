import { afterEach, describe, expect, it } from "vitest";

import { ACCESS_PROOF_MS, forgetAccessToken, holdAccessToken, takeAccessToken } from "./accessProof";

afterEach(() => {
  forgetAccessToken();
});

describe("the access token held for the first administrator", () => {
  it("is handed over once and then forgotten", () => {
    holdAccessToken("noust_tok_abc", 1_000);
    expect(takeAccessToken(1_001)).toBe("noust_tok_abc");
    expect(takeAccessToken(1_002)).toBeNull();
  });

  it("is forgotten when its time has passed, and when the sign-in went somewhere else", () => {
    holdAccessToken("noust_tok_abc", 1_000);
    expect(takeAccessToken(1_000 + ACCESS_PROOF_MS)).toBeNull();
    holdAccessToken("noust_tok_abc", 1_000);
    forgetAccessToken();
    expect(takeAccessToken(1_001)).toBeNull();
  });

  it("holds nothing for an empty token", () => {
    holdAccessToken("", 1_000);
    expect(takeAccessToken(1_001)).toBeNull();
  });

  it("is never written to storage", () => {
    holdAccessToken("noust_tok_abc");
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain("noust_tok_abc");
    expect(document.cookie).not.toContain("noust_tok_abc");
  });
});
