/**
 * What an operator pasted as a join code, read before anything is sent: the one line
 * `noust fleet authorize` prints on the server being added (`noust-join:v1:` and base64url
 * JSON), mirroring `noust.fleet.joincode` (JOIN_PREFIX). A console or API token pasted by
 * mistake, a code from a newer Noust, or the whole terminal block around the code are told
 * apart here, so the operator is told what they pasted instead of a refusal from the server.
 *
 * The code carries a token: nothing here ever repeats the value, and the summary read from it
 * leaves the token out.
 */

export const JOIN_PREFIX = "noust-join:v1:";

/** The code on its own line, wherever it is in what was pasted. */
const CODE = /noust-join:v1:[A-Za-z0-9_-]+/;
const OTHER_VERSION = /noust-join:(?!v1:)[A-Za-z0-9]+:/;

export type JoinCodeProblem = "empty" | "apiToken" | "consoleToken" | "newer" | "wrongPrefix" | "unreadable";

export type JoinCodeReading = { ok: true; code: string; summary: JoinCodeSummary } | { ok: false; problem: JoinCodeProblem };

/** What the code says, without its token: shown so the operator can check it is the right one. */
export interface JoinCodeSummary {
  /** The name the server suggests for itself. */
  node: string | null;
  sshUser: string;
  sshPort: number;
  consolePort: number;
  version: string;
  /** The central the server authorized, by the name it was given. */
  central: string | null;
  /** The fingerprint of the central's key the server authorized. */
  centralKeyFingerprint: string;
}

function decodeBase64Url(payload: string): string | null {
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (payload.length % 4)) % 4);
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    // Not base64url, or not UTF-8: not a join code, whatever its prefix says.
    return null;
  }
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function port(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 && value < 65536 ? value : null;
}

/** Reads the summary out of a code's payload; null when it is not what a node prints. */
export function summaryOf(code: string): JoinCodeSummary | null {
  const json = decodeBase64Url(code.slice(JOIN_PREFIX.length));
  if (json === null) return null;
  let document: unknown;
  try {
    document = JSON.parse(json);
  } catch {
    // Decoded, but not JSON.
    return null;
  }
  if (typeof document !== "object" || document === null || Array.isArray(document)) return null;
  const fields = document as Record<string, unknown>;
  const sshUser = text(fields["ssh_user"]);
  const sshPort = port(fields["ssh_port"]);
  const consolePort = port(fields["console_port"]);
  const version = text(fields["noust_version"]);
  const fingerprint = text(fields["central_key_fp"]);
  if (sshUser === null || sshPort === null || consolePort === null || version === null || fingerprint === null || text(fields["token"]) === null) return null;
  return {
    node: text(fields["node_name"]),
    sshUser,
    sshPort,
    consolePort,
    version,
    central: text(fields["central"]),
    centralKeyFingerprint: fingerprint,
  };
}

/**
 * Tells what was pasted: a join code (the line itself, found even inside the whole block the
 * terminal printed), or which mistake it is.
 */
export function classifyJoinCode(pasted: string): JoinCodeReading {
  const value = pasted.trim();
  if (value === "") return { ok: false, problem: "empty" };
  const match = CODE.exec(value);
  if (match !== null) {
    const summary = summaryOf(match[0]);
    return summary === null ? { ok: false, problem: "unreadable" } : { ok: true, code: match[0], summary };
  }
  if (OTHER_VERSION.test(value)) return { ok: false, problem: "newer" };
  // A token of this console's own: an API token (noust_tok_/wasm_tok_) or the access token
  // printed when the console was installed (noust_/wasm_).
  if (/(?:^|\s)(?:noust|wasm)_tok_/.test(value)) return { ok: false, problem: "apiToken" };
  if (/(?:^|\s)(?:noust|wasm)_[A-Za-z0-9_-]{8,}/.test(value)) return { ok: false, problem: "consoleToken" };
  return { ok: false, problem: "wrongPrefix" };
}

/**
 * The SHA-256 fingerprint of an SSH public key line, as OpenSSH prints it (`SHA256:...`), to
 * check that a code was made for the key this central showed. Null where the browser offers no
 * Web Crypto (a console reached over plain HTTP on a network address): the central checks it
 * anyway when the code is sent.
 */
export async function sshFingerprint(publicKey: string): Promise<string | null> {
  const blob = publicKey.trim().split(/\s+/)[1];
  if (blob === undefined || typeof crypto === "undefined" || typeof crypto.subtle === "undefined") return null;
  let bytes: Uint8Array;
  try {
    bytes = Uint8Array.from(atob(blob), (char) => char.charCodeAt(0));
  } catch {
    // Not base64: not a key line.
    return null;
  }
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>));
  const base64 = btoa(String.fromCharCode(...digest)).replace(/=+$/, "");
  return `SHA256:${base64}`;
}
