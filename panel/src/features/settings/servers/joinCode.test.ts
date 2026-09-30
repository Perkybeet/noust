import { describe, expect, it } from "vitest";

import { classifyJoinCode, sshFingerprint, summaryOf } from "./joinCode";

/** A join code the way `noust fleet authorize` spells one: the prefix and base64url JSON. */
export function joinCodeOf(fields: Record<string, unknown>): string {
  const base64 = btoa(JSON.stringify(fields)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `noust-join:v1:${base64}`;
}

export const CODE_FIELDS = {
  ssh_host_key_line: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB",
  ssh_user: "noust-tunnel",
  ssh_port: 22,
  console_port: 8080,
  token: "noust_tok_abcdefghijklmnopqrstuvwxyz0123456789",
  noust_version: "3.1.0",
  central_key_fp: "SHA256:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU",
  node_name: "web-3",
  central: "nas",
};

describe("what was pasted as a join code", () => {
  it("reads a code, and what it says without its token", () => {
    const code = joinCodeOf(CODE_FIELDS);
    const reading = classifyJoinCode(code);
    expect(reading.ok).toBe(true);
    if (!reading.ok) return;
    expect(reading.code).toBe(code);
    expect(reading.summary).toEqual({
      node: "web-3",
      sshUser: "noust-tunnel",
      sshPort: 22,
      consolePort: 8080,
      version: "3.1.0",
      central: "nas",
      centralKeyFingerprint: CODE_FIELDS.central_key_fp,
    });
    expect(JSON.stringify(reading.summary)).not.toContain("noust_tok_");
  });

  it("finds the code inside the whole block the terminal printed", () => {
    const code = joinCodeOf(CODE_FIELDS);
    const reading = classifyJoinCode(`Authorized nas.\n\nJoin code:\n  ${code}\n\nThen, on the central: noust node add web-3 ...`);
    expect(reading).toMatchObject({ ok: true, code });
  });

  it("tells each mistake apart, never repeating the value", () => {
    expect(classifyJoinCode("   ")).toEqual({ ok: false, problem: "empty" });
    expect(classifyJoinCode("noust_tok_abcdefghijklmnopqrstu")).toEqual({ ok: false, problem: "apiToken" });
    expect(classifyJoinCode("wasm_tok_abcdefghijklmnopqrstu")).toEqual({ ok: false, problem: "apiToken" });
    expect(classifyJoinCode("noust_Zq9xY8wV7uT6sR5qP4oN3m")).toEqual({ ok: false, problem: "consoleToken" });
    expect(classifyJoinCode("wasm_Zq9xY8wV7uT6sR5qP4oN3m")).toEqual({ ok: false, problem: "consoleToken" });
    expect(classifyJoinCode("noust-join:v2:eyJ4Ijox")).toEqual({ ok: false, problem: "newer" });
    expect(classifyJoinCode("hello there")).toEqual({ ok: false, problem: "wrongPrefix" });
    expect(classifyJoinCode("noust-join:v1:bm90IGpzb24")).toEqual({ ok: false, problem: "unreadable" });
  });

  it("refuses a payload missing what a node always prints", () => {
    const withoutToken: Record<string, unknown> = { ...CODE_FIELDS };
    delete withoutToken["token"];
    expect(summaryOf(joinCodeOf(withoutToken))).toBeNull();
    expect(summaryOf(joinCodeOf({ ...CODE_FIELDS, ssh_port: 70000 }))).toBeNull();
  });
});

describe("a key's fingerprint", () => {
  it("is OpenSSH's SHA256 of the key's blob", async () => {
    // A line with no key in it has no fingerprint.
    expect(await sshFingerprint("ssh-ed25519")).toBeNull();
    const fingerprint = await sshFingerprint("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB central@nas");
    expect(fingerprint).toMatch(/^SHA256:[A-Za-z0-9+/]{43}$/);
  });
});
