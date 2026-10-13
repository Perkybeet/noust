import { describe, expect, it } from "vitest";

import { classifyLinkCode, linkSummaryOf } from "./link";

/** A link code the way `noust fleet link` spells one: the prefix and base64url JSON. */
export function linkCodeOf(fields: Record<string, unknown>): string {
  const base64 = btoa(JSON.stringify(fields)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `noust-link:v1:${base64}`;
}

export const LINK_FIELDS = {
  node_name: "web-5",
  central: "nas",
  link_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB",
  console_port: 8080,
  token: "noust_tok_abcdefghijklmnopqrstuvwxyz0123456789",
  noust_version: "3.4.0",
  nonce: "0123456789abcdef0123456789abcdef",
};

describe("what was pasted as a link code", () => {
  it("reads a code, and what it says without its token or its key", () => {
    const code = linkCodeOf(LINK_FIELDS);
    const reading = classifyLinkCode(code);
    expect(reading.ok).toBe(true);
    if (!reading.ok) return;
    expect(reading.code).toBe(code);
    expect(reading.summary).toEqual({ node: "web-5", central: "nas", consolePort: 8080, version: "3.4.0" });
    expect(JSON.stringify(reading.summary)).not.toContain("noust_tok_");
  });

  it("finds the code inside the whole block the terminal printed", () => {
    const code = linkCodeOf(LINK_FIELDS);
    const reading = classifyLinkCode(`This server dials nas.\n\nLink code:\n  ${code}\n\nOn the central: noust node link add web-5 --link-code -`);
    expect(reading).toMatchObject({ ok: true, code });
  });

  it("tells each mistake apart, never repeating the value", () => {
    expect(classifyLinkCode("   ")).toEqual({ ok: false, problem: "empty" });
    // A join code is for a server the central reaches over SSH: the other way of adding one.
    expect(classifyLinkCode("noust-join:v1:eyJ4IjoxfQ")).toEqual({ ok: false, problem: "joinCode" });
    expect(classifyLinkCode("noust_tok_abcdefghijklmnopqrstu")).toEqual({ ok: false, problem: "apiToken" });
    expect(classifyLinkCode("noust_Zq9xY8wV7uT6sR5qP4oN3m")).toEqual({ ok: false, problem: "consoleToken" });
    expect(classifyLinkCode("noust-link:v2:eyJ4Ijox")).toEqual({ ok: false, problem: "newer" });
    expect(classifyLinkCode("hello there")).toEqual({ ok: false, problem: "wrongPrefix" });
    expect(classifyLinkCode("noust-link:v1:bm90IGpzb24")).toEqual({ ok: false, problem: "unreadable" });
  });

  it("refuses a payload missing what a server always prints", () => {
    const withoutToken: Record<string, unknown> = { ...LINK_FIELDS };
    delete withoutToken["token"];
    expect(linkSummaryOf(linkCodeOf(withoutToken))).toBeNull();
    const withoutKey: Record<string, unknown> = { ...LINK_FIELDS };
    delete withoutKey["link_key"];
    expect(linkSummaryOf(linkCodeOf(withoutKey))).toBeNull();
    expect(linkSummaryOf(linkCodeOf({ ...LINK_FIELDS, console_port: 0 }))).toBeNull();
    expect(linkSummaryOf(linkCodeOf({ ...LINK_FIELDS, console_port: "8080" }))).toBeNull();
    // Valid JSON, but not an object.
    expect(linkSummaryOf("noust-link:v1:WzEsMl0")).toBeNull();
  });
});
