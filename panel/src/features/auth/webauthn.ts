/**
 * WebAuthn without a library: the browser's `navigator.credentials` with the options the
 * server sends in WebAuthn's JSON form, and the credential sent back in the same form.
 *
 * Browsers from 2025 on do the conversion themselves (`parseCreationOptionsFromJSON`,
 * `parseRequestOptionsFromJSON`, `credential.toJSON()`); for the ones before them the few
 * members that are bytes are converted here, base64url by hand. Nothing touches the DOM, so
 * Trusted Types has nothing to say, and the ceremony is not a network request, so the CSP's
 * `connect-src 'self'` is all it needs.
 *
 * A ceremony the browser refuses is reported with its own words (a DOMException's name and
 * message), never paraphrased: "NotAllowedError: The operation either timed out or was not
 * allowed" is what the operator needs to search for.
 *
 * A page has one passkey window at a time, and the browser is the one that says when the last one
 * is over, a moment after its promise settles. The two ceremonies below go through one gate
 * (`ceremony`) that never lets two overlap, and that asks again, a few times and briefly, when the
 * browser answers that the previous one is still closing.
 */

import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";

/** JSON as the server sends it (`CeremonyOptions.public_key`). */
export type JsonObject = Record<string, unknown>;

// ---------------------------------------------------------------------------------------
// base64url

/** Bytes as base64url without padding, the encoding WebAuthn's JSON uses. */
export function toBase64url(buffer: ArrayBuffer | ArrayBufferView): string {
  const bytes = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

/** base64url (padded or not) as bytes. */
export function fromBase64url(value: string): ArrayBuffer {
  const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

// ---------------------------------------------------------------------------------------
// What this browser can do

interface PublicKeyCredentialStatics {
  isConditionalMediationAvailable?: () => Promise<boolean>;
  parseCreationOptionsFromJSON?: (options: JsonObject) => PublicKeyCredentialCreationOptions;
  parseRequestOptionsFromJSON?: (options: JsonObject) => PublicKeyCredentialRequestOptions;
}

function statics(): PublicKeyCredentialStatics | null {
  const constructor = (globalThis as { PublicKeyCredential?: unknown }).PublicKeyCredential;
  return typeof constructor === "function" ? (constructor as PublicKeyCredentialStatics) : null;
}

/** Why this page cannot start a ceremony, as far as the browser alone knows; null when it can. */
export type BrowserLimit = "insecure_context" | "no_webauthn" | "ip_address";

/** Whether a host is an address: browsers never bind a passkey to one. */
export function isAddress(host: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(":") || host.startsWith("[");
}

export function browserLimit(): BrowserLimit | null {
  if (!globalThis.isSecureContext) return "insecure_context";
  if (typeof location !== "undefined" && isAddress(location.hostname)) return "ip_address";
  if (statics() === null || typeof navigator === "undefined" || !("credentials" in navigator)) return "no_webauthn";
  return null;
}

/** Whether the browser offers passkeys in a field's autofill (conditional UI). */
export async function conditionalMediationAvailable(): Promise<boolean> {
  const check = statics()?.isConditionalMediationAvailable;
  if (browserLimit() !== null || check === undefined) return false;
  try {
    return await check.call(statics());
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------------------
// Options: JSON to what navigator.credentials takes

function bytesOf(value: unknown): ArrayBuffer {
  if (typeof value !== "string") throw new TypeError("WebAuthn options carry bytes as base64url strings");
  return fromBase64url(value);
}

function descriptors(value: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.map((entry) => {
    const item = entry as { id: unknown; type?: unknown; transports?: unknown };
    const descriptor: PublicKeyCredentialDescriptor = { ...(item as object), type: "public-key", id: bytesOf(item.id) };
    return descriptor;
  });
}

/** Creation options from their JSON form: the browser's own parser when it has one. */
export function creationOptions(json: JsonObject): PublicKeyCredentialCreationOptions {
  const native = statics()?.parseCreationOptionsFromJSON;
  if (native !== undefined) return native.call(statics(), json);
  const user = json["user"] as JsonObject;
  const exclude = descriptors(json["excludeCredentials"]);
  return {
    ...(json as object),
    challenge: bytesOf(json["challenge"]),
    user: { ...(user as object), id: bytesOf(user["id"]) } as PublicKeyCredentialUserEntity,
    ...(exclude !== undefined ? { excludeCredentials: exclude } : {}),
  } as PublicKeyCredentialCreationOptions;
}

/** Request options from their JSON form: the browser's own parser when it has one. */
export function requestOptions(json: JsonObject): PublicKeyCredentialRequestOptions {
  const native = statics()?.parseRequestOptionsFromJSON;
  if (native !== undefined) return native.call(statics(), json);
  const allow = descriptors(json["allowCredentials"]);
  const options: PublicKeyCredentialRequestOptions = {
    ...(json as object),
    challenge: bytesOf(json["challenge"]),
    ...(allow !== undefined ? { allowCredentials: allow } : {}),
  };
  return options;
}

// ---------------------------------------------------------------------------------------
// The credential: back to JSON

interface CredentialLike {
  id: string;
  rawId: ArrayBuffer;
  type: string;
  authenticatorAttachment?: string | null;
  response: {
    clientDataJSON: ArrayBuffer;
    attestationObject?: ArrayBuffer;
    authenticatorData?: ArrayBuffer;
    signature?: ArrayBuffer;
    userHandle?: ArrayBuffer | null;
    getTransports?: () => string[];
  };
  getClientExtensionResults?: () => AuthenticationExtensionsClientOutputs;
  toJSON?: () => JsonObject;
}

/** A credential in WebAuthn's JSON form (RegistrationResponseJSON / AuthenticationResponseJSON). */
export function credentialJson(credential: Credential): JsonObject {
  const found = credential as unknown as CredentialLike;
  if (typeof found.toJSON === "function") return found.toJSON();
  const { response } = found;
  const body: JsonObject = { clientDataJSON: toBase64url(response.clientDataJSON) };
  if (response.attestationObject !== undefined) {
    body["attestationObject"] = toBase64url(response.attestationObject);
    body["transports"] = typeof response.getTransports === "function" ? response.getTransports() : [];
  }
  if (response.authenticatorData !== undefined) body["authenticatorData"] = toBase64url(response.authenticatorData);
  if (response.signature !== undefined) body["signature"] = toBase64url(response.signature);
  if (response.userHandle !== undefined && response.userHandle !== null) body["userHandle"] = toBase64url(response.userHandle);
  return {
    id: toBase64url(found.rawId),
    rawId: toBase64url(found.rawId),
    type: found.type,
    authenticatorAttachment: found.authenticatorAttachment ?? null,
    clientExtensionResults: typeof found.getClientExtensionResults === "function" ? found.getClientExtensionResults() : {},
    response: body,
  };
}

// ---------------------------------------------------------------------------------------
// Ceremonies

/** The browser refused or abandoned a ceremony; `detail` is its own words. */
export class CeremonyError extends Error {
  /** The DOMException's name: NotAllowedError, InvalidStateError, SecurityError... */
  readonly reason: string;
  /** "NotAllowedError: The operation either timed out or was not allowed." */
  readonly detail: string;
  /** The operator closed the browser's prompt or it timed out: nothing to report loudly. */
  readonly cancelled: boolean;
  /**
   * What the console says above the browser's words when it knows why they came (the browser was still
   * closing another passkey window). `describeError` reads it like the fix an API error carries.
   */
  readonly hint?: string;

  constructor(reason: string, message: string, hint?: string) {
    super(message);
    this.name = "CeremonyError";
    this.reason = reason;
    this.detail = message === "" ? reason : `${reason}: ${message}`;
    this.cancelled = reason === "NotAllowedError" || reason === "AbortError";
    if (hint !== undefined) this.hint = hint;
  }
}

function ceremonyError(cause: unknown): CeremonyError {
  if (cause instanceof CeremonyError) return cause;
  if (cause instanceof DOMException) return new CeremonyError(cause.name, cause.message);
  if (cause instanceof Error) return new CeremonyError(cause.name, cause.message);
  return new CeremonyError("Error", String(cause));
}

/**
 * The browser refused a ceremony because it still had another one open: what it says is the name of a
 * DOMException that says "operation" or "state" and a message that says "pending". Matched on both, so
 * a refusal of another kind that happens to say "pending" is not asked again.
 *
 * What each browser says:
 * - Chrome, Edge and the other Chromium browsers: `OperationError: A request is already pending.` (what
 *   an operator met in production, and Blink's `PENDING_REQUEST`). An older Chromium gave the same words
 *   as an `InvalidStateError`, which is why both names are matched.
 * - Safari and Firefox: not measured here. WebKit's own reports of a second request while one is open
 *   say `InvalidStateError: A credential picker operation is already in progress`, which does not say
 *   "pending" and is therefore shown as it came and not asked again; a version of either that answers
 *   an `OperationError` or `InvalidStateError` with "pending" in it is matched here by its word.
 * Nothing else is taken for it: `NotAllowedError` is the operator closing the window, and a
 * `SecurityError` is a name the page may not bind a passkey to, which no waiting mends.
 */
export function isPendingRefusal(cause: unknown): boolean {
  // Read by its fields: the error of a frame or of a test's DOM is not an `instanceof Error` of this realm.
  if (typeof cause !== "object" || cause === null) return false;
  const { name, message } = cause as { name?: unknown; message?: unknown };
  return (name === "OperationError" || name === "InvalidStateError") && typeof message === "string" && /pending/i.test(message);
}

type Pause = (milliseconds: number) => Promise<void>;

const realPause: Pause = (milliseconds) =>
  new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });

let pause: Pause = realPause;

/** For tests: what waits between two attempts, so none of them waits for a clock. `null` puts the real one back. */
export function setCeremonyPause(replacement: Pause | null): void {
  pause = replacement ?? realPause;
}

/** The waits before each new attempt after a "pending" answer: 1.75 seconds in all, a little longer each time. */
export const PENDING_RETRY_DELAYS_MS: readonly number[] = [250, 500, 1000];

async function withRetries<T>(call: () => Promise<T>, signal: AbortSignal): Promise<T> {
  for (let retry = 0; ; retry += 1) {
    try {
      return await call();
    } catch (cause: unknown) {
      const error = ceremonyError(cause);
      if (!isPendingRefusal(cause)) throw error;
      const delay = PENDING_RETRY_DELAYS_MS[retry];
      // Still pending after the last wait: the browser's words, with what the console knows of them above.
      if (delay === undefined) throw new CeremonyError(error.reason, error.message, translate(getLocale(), "auth.passkeyStillClosing"));
      await pause(delay);
      if (signal.aborted) throw new CeremonyError("AbortError", "");
    }
  }
}

/** The ceremony that holds the page's one passkey window: `conditional` is the field's autofill, which an explicit one displaces. */
interface Slot {
  readonly conditional: boolean;
  abort: () => void;
  /** Settled once the browser has answered (or refused), whatever the answer was. */
  done: Promise<void>;
}

let slot: Slot | null = null;

/**
 * The most an explicit ceremony waits for the autofill it ended to say so. A browser answers an aborted
 * ceremony at once; one that never does would otherwise hold the sign-in button for ever, and what is asked
 * of it after this is still covered by the retry of a "pending" answer.
 */
export const SETTLE_LIMIT_MS = 2000;

async function endedWithin(previous: Slot): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const limit = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, SETTLE_LIMIT_MS);
  });
  try {
    await Promise.race([previous.done, limit]);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The one door of every ceremony. At most one is open: an autofill (conditional) one is ended, with its
 * own `AbortController`, and waited for before an explicit one or another autofill starts; an explicit
 * one is never displaced, so a second explicit call, or an autofill that would take its place, is
 * refused as cancelled without the browser hearing of it. The slot is taken before the first `await`,
 * so two calls in one tick cannot both pass.
 */
function ceremony<T>(conditional: boolean, caller: AbortSignal | undefined, call: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const before = slot;
  if (before !== null && !before.conditional) {
    return Promise.reject(new CeremonyError("AbortError", "Another passkey window is already open."));
  }
  const own = new AbortController();
  const forward = (): void => {
    own.abort();
  };
  if (caller?.aborted === true) own.abort();
  else caller?.addEventListener("abort", forward, { once: true });
  const mine: Slot = { conditional, abort: forward, done: Promise.resolve() };
  slot = mine;
  const run = (async (): Promise<T> => {
    try {
      if (before !== null) {
        before.abort();
        await endedWithin(before);
      }
      if (own.signal.aborted) throw new CeremonyError("AbortError", "");
      return await withRetries(() => call(own.signal), own.signal);
    } finally {
      caller?.removeEventListener("abort", forward);
      // Freed before the caller's own continuation runs, which may well ask for the next ceremony.
      if (slot === mine) slot = null;
    }
  })();
  mine.done = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

/** Creates a passkey from the server's creation options; the returned JSON goes back to it. */
export function createPasskey(json: JsonObject): Promise<JsonObject> {
  return ceremony(false, undefined, async () => {
    const credential = await navigator.credentials.create({ publicKey: creationOptions(json) });
    if (credential === null) throw new CeremonyError("NotAllowedError", "");
    return credentialJson(credential);
  });
}

export interface GetOptions {
  /** `conditional` offers the passkey in a field's autofill instead of a prompt. */
  mediation?: CredentialMediationRequirement | undefined;
  signal?: AbortSignal | undefined;
}

/** Asks for a passkey (sign-in or confirmation); the returned JSON goes back to the server. */
export function getPasskey(json: JsonObject, { mediation, signal }: GetOptions = {}): Promise<JsonObject> {
  return ceremony(mediation === "conditional", signal, async (own) => {
    const credential = await navigator.credentials.get({
      publicKey: requestOptions(json),
      ...(mediation !== undefined ? { mediation } : {}),
      signal: own,
    });
    if (credential === null) throw new CeremonyError("NotAllowedError", "");
    return credentialJson(credential);
  });
}

/** What a new passkey could be called: the browser and the system it runs on (product names, not translated). */
export function browserAndSystem(userAgent: string = typeof navigator === "undefined" ? "" : navigator.userAgent): {
  browser: string;
  system: string | null;
} {
  const browser = userAgent.includes("Edg/")
    ? "Edge"
    : userAgent.includes("Firefox/")
      ? "Firefox"
      : userAgent.includes("Chrome/")
        ? "Chrome"
        : userAgent.includes("Safari/")
          ? "Safari"
          : "Browser";
  const system = /iPhone|iPad/.test(userAgent)
    ? "iOS"
    : userAgent.includes("Android")
      ? "Android"
      : userAgent.includes("Mac OS X")
        ? "macOS"
        : userAgent.includes("Windows")
          ? "Windows"
          : userAgent.includes("Linux")
            ? "Linux"
            : null;
  return { browser, system };
}
