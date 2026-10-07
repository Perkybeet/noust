/**
 * What the guided enrollment asks and sends: a server's name, address and account, one
 * credential (a password or a private key), the ceiling this central is given there, and the
 * host key fingerprint the operator confirmed. The credential is only ever in the form; the
 * request that carries it is built here and the form is emptied of it as soon as it is sent.
 */

import type { BodyOf } from "../../../api/client";
import { NODE_NAME } from "../nodes";

export type EnrollBody = BodyOf<"/api/fleet/enroll", "post">;

export type Credential = "password" | "key";
export type Access = "admin" | "deploy" | "read";

export interface EnrollForm {
  name: string;
  host: string;
  port: string;
  user: string;
  credential: Credential;
  password: string;
  privateKey: string;
  passphrase: string;
  access: Access;
  hostAccess: boolean;
  install: boolean;
  replaceToken: boolean;
}

export const EMPTY_ENROLL: EnrollForm = {
  name: "",
  host: "",
  port: "22",
  user: "root",
  credential: "password",
  password: "",
  privateKey: "",
  passphrase: "",
  access: "admin",
  hostAccess: false,
  install: true,
  replaceToken: false,
};

export type Problem = "name" | "host" | "port" | "user" | "password" | "key" | "keyShape";

/** A private key's text starts with its BEGIN line, whatever the kind of key. */
export function looksLikePrivateKey(text: string): boolean {
  return /^\s*-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/.test(text);
}

/** A port as typed: whole, 1 to 65535. */
export function validPort(text: string): boolean {
  const port = Number(text.trim());
  return /^\d+$/.test(text.trim()) && port >= 1 && port <= 65535;
}

/** The first thing wrong with the form, or null: the order the fields are in. */
export function firstProblem(form: EnrollForm): Problem | null {
  if (!NODE_NAME.test(form.name.trim())) return "name";
  if (form.host.trim() === "") return "host";
  if (!validPort(form.port)) return "port";
  if (form.user.trim() === "") return "user";
  if (form.credential === "password") return form.password === "" ? "password" : null;
  if (form.privateKey.trim() === "") return "key";
  return looksLikePrivateKey(form.privateKey) ? null : "keyShape";
}

/** The request that asks which host key answers: the address only, no credential. */
export function hostKeyBody(form: EnrollForm): { host: string; port: number } {
  return { host: form.host.trim(), port: Number(form.port.trim()) };
}

/**
 * The request that enrolls the server: the fingerprint the operator confirmed and the one
 * credential the form holds (the other kind is never sent, whatever was typed before).
 */
export function enrollBody(form: EnrollForm, fingerprint: string): EnrollBody {
  return {
    name: form.name.trim(),
    host: form.host.trim(),
    port: Number(form.port.trim()),
    ssh_user: form.user.trim(),
    host_key_fingerprint: fingerprint,
    access: form.access,
    allow_host_access: form.hostAccess,
    install: form.install,
    replace_token: form.replaceToken,
    password: form.credential === "password" ? form.password : null,
    private_key: form.credential === "key" ? form.privateKey : null,
    passphrase: form.credential === "key" && form.passphrase !== "" ? form.passphrase : null,
  };
}

/** The form with every part of the credential emptied: what stays on the page once it is sent. */
export function withoutCredential(form: EnrollForm): EnrollForm {
  return { ...form, password: "", privateKey: "", passphrase: "" };
}
