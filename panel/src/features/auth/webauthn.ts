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
 */

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

  constructor(reason: string, message: string) {
    super(message);
    this.name = "CeremonyError";
    this.reason = reason;
    this.detail = message === "" ? reason : `${reason}: ${message}`;
    this.cancelled = reason === "NotAllowedError" || reason === "AbortError";
  }
}

function ceremonyError(cause: unknown): CeremonyError {
  if (cause instanceof CeremonyError) return cause;
  if (cause instanceof DOMException) return new CeremonyError(cause.name, cause.message);
  if (cause instanceof Error) return new CeremonyError(cause.name, cause.message);
  return new CeremonyError("Error", String(cause));
}

/** Creates a passkey from the server's creation options; the returned JSON goes back to it. */
export async function createPasskey(json: JsonObject): Promise<JsonObject> {
  try {
    const credential = await navigator.credentials.create({ publicKey: creationOptions(json) });
    if (credential === null) throw new CeremonyError("NotAllowedError", "");
    return credentialJson(credential);
  } catch (cause: unknown) {
    throw ceremonyError(cause);
  }
}

export interface GetOptions {
  /** `conditional` offers the passkey in a field's autofill instead of a prompt. */
  mediation?: CredentialMediationRequirement | undefined;
  signal?: AbortSignal | undefined;
}

/** Asks for a passkey (sign-in or confirmation); the returned JSON goes back to the server. */
export async function getPasskey(json: JsonObject, { mediation, signal }: GetOptions = {}): Promise<JsonObject> {
  try {
    const credential = await navigator.credentials.get({
      publicKey: requestOptions(json),
      ...(mediation !== undefined ? { mediation } : {}),
      ...(signal !== undefined ? { signal } : {}),
    });
    if (credential === null) throw new CeremonyError("NotAllowedError", "");
    return credentialJson(credential);
  } catch (cause: unknown) {
    throw ceremonyError(cause);
  }
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
