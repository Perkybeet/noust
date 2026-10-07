import { describe, expect, it } from "vitest";

import { EMPTY_ENROLL, enrollBody, firstProblem, hostKeyBody, looksLikePrivateKey, validPort, withoutCredential } from "./data";
import type { EnrollForm } from "./data";

const FILLED: EnrollForm = { ...EMPTY_ENROLL, name: "web-2", host: "203.0.113.9", password: "hunter2-but-longer" };
const KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk=\n-----END OPENSSH PRIVATE KEY-----\n";

describe("the guided enrollment's form", () => {
  it("asks for the name, the address, the port, the account and the credential, in that order", () => {
    expect(firstProblem(EMPTY_ENROLL)).toBe("name");
    expect(firstProblem({ ...EMPTY_ENROLL, name: "Web 2" })).toBe("name");
    expect(firstProblem({ ...EMPTY_ENROLL, name: "web-2" })).toBe("host");
    expect(firstProblem({ ...FILLED, port: "99999" })).toBe("port");
    expect(firstProblem({ ...FILLED, user: " " })).toBe("user");
    expect(firstProblem({ ...FILLED, password: "" })).toBe("password");
    expect(firstProblem(FILLED)).toBeNull();
  });

  it("takes a private key that starts like one, and says when it does not", () => {
    expect(firstProblem({ ...FILLED, credential: "key", privateKey: "" })).toBe("key");
    expect(firstProblem({ ...FILLED, credential: "key", privateKey: "ssh-ed25519 AAAA public" })).toBe("keyShape");
    expect(firstProblem({ ...FILLED, credential: "key", privateKey: KEY })).toBeNull();
    expect(looksLikePrivateKey("-----BEGIN RSA PRIVATE KEY-----\nxx")).toBe(true);
    expect(looksLikePrivateKey("-----BEGIN PUBLIC KEY-----")).toBe(false);
  });

  it("takes a port from 1 to 65535", () => {
    expect(validPort("22")).toBe(true);
    expect(validPort(" 2222 ")).toBe(true);
    expect(validPort("0")).toBe(false);
    expect(validPort("65536")).toBe(false);
    expect(validPort("2e2")).toBe(false);
  });

  it("asks which host key answers with the address alone, never the credential", () => {
    expect(hostKeyBody({ ...FILLED, host: " 203.0.113.9 ", port: "2222" })).toEqual({ host: "203.0.113.9", port: 2222 });
  });
});

describe("the request that enrolls", () => {
  it("carries the confirmed fingerprint and the password, and no key", () => {
    expect(enrollBody({ ...FILLED, privateKey: KEY, passphrase: "left over" }, "SHA256:abc")).toEqual({
      name: "web-2",
      host: "203.0.113.9",
      port: 22,
      ssh_user: "root",
      host_key_fingerprint: "SHA256:abc",
      access: "admin",
      allow_host_access: false,
      install: true,
      replace_token: false,
      password: "hunter2-but-longer",
      private_key: null,
      passphrase: null,
    });
  });

  it("carries the key and its passphrase, and no password, when a key is the way in", () => {
    const body = enrollBody({ ...FILLED, credential: "key", privateKey: KEY, passphrase: "open sesame", password: "left over", access: "deploy", hostAccess: true }, "SHA256:abc");
    expect(body).toMatchObject({ password: null, private_key: KEY, passphrase: "open sesame", access: "deploy", allow_host_access: true });
  });

  it("leaves the passphrase out when the key has none", () => {
    expect(enrollBody({ ...FILLED, credential: "key", privateKey: KEY }, "SHA256:abc").passphrase).toBeNull();
  });

  it("is emptied of every part of the credential once sent", () => {
    expect(withoutCredential({ ...FILLED, credential: "key", privateKey: KEY, passphrase: "open sesame" })).toMatchObject({ password: "", privateKey: "", passphrase: "", name: "web-2", host: "203.0.113.9" });
  });
});
