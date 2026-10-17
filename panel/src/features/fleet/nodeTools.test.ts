import { describe, expect, it } from "vitest";

import { BASTION, ED25519_LINE, FINGERPRINT, isInventoryPart, parseBastionKey, parseTimeout } from "./nodeTools";

const PRINT = `SHA256:${"A".repeat(43)}`;
const LINE = `ssh-ed25519 ${"A".repeat(68)}`;

describe("the wait for a server's connection", () => {
  it("is whole seconds from 1 to 300, or nothing for the default", () => {
    expect(parseTimeout("")).toBeNull();
    expect(parseTimeout("   ")).toBeNull();
    expect(parseTimeout("1")).toBe(1);
    expect(parseTimeout(" 300 ")).toBe(300);
    for (const bad of ["0", "301", "-5", "1.5", "ten", "1e2", "0300", "1000"]) expect(parseTimeout(bad), bad).toBeUndefined();
  });
});

describe("a bastion as typed", () => {
  it("is user@host with an optional port, where the host is a name or an address", () => {
    for (const good of ["hop@bastion.example.com", "hop@bastion.example.com:2222", "hop@203.0.113.7:22", "hop@[2001:db8::1]:22", "_svc-1@b"]) expect(BASTION.test(good), good).toBe(true);
  });

  it("refuses what ssh would read as an option, a second destination or a shell", () => {
    for (const bad of ["bastion.example.com", "-oProxyCommand=x@b", "hop@-b", "hop@b c", "hop@b;id", "hop@b\n", "a@b@c", "hop@b:", "hop@b:port", "@b", "Hop@b"]) {
      expect(BASTION.test(bad), JSON.stringify(bad)).toBe(false);
    }
  });
});

describe("a bastion's host key as typed", () => {
  it("is a fingerprint or an ed25519 line, or nothing to keep the pinned key", () => {
    expect(parseBastionKey("")).toBeNull();
    expect(parseBastionKey(` ${PRINT} `)).toEqual({ fingerprint: PRINT });
    expect(parseBastionKey(LINE)).toEqual({ host_key: LINE });
    expect(parseBastionKey(`${LINE} root@bastion`)).toEqual({ host_key: `${LINE} root@bastion` });
  });

  it("is never guessed at: a key of another kind or a damaged print is refused", () => {
    for (const bad of ["SHA256:short", "MD5:aa:bb", "ssh-rsa AAAA", `${PRINT}\nextra`, "bastion.example.com"]) expect(parseBastionKey(bad), JSON.stringify(bad)).toBeUndefined();
    expect(FINGERPRINT.test(`${PRINT}\n`)).toBe(false);
    expect(ED25519_LINE.test(`${LINE}\n`)).toBe(false);
  });
});

describe("the parts of an inventory", () => {
  it("knows the five the central reports and nothing else", () => {
    for (const part of ["labels", "placements", "monitoring", "ssh", "cloud"]) expect(isInventoryPart(part), part).toBe(true);
    expect(isInventoryPart("secrets")).toBe(false);
  });
});
