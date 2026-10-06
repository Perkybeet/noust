/**
 * What the sign-in page is told when it is sent back from an identity provider: `?sso=<ticket>`
 * when the provider's answer was accepted (the page collects it), `?sso_error=<word>` when it
 * was not. One word, never the reason: the reason is in the server's audit log.
 */

/** The words the server uses (`SSO_ERRORS` in `noust/web/api/oidc.py`). */
export const SSO_ERRORS = ["unavailable", "expired", "denied", "failed", "refused", "locked"] as const;

export type SsoError = (typeof SSO_ERRORS)[number];

/** The word, when the address carried one the console knows; anything else is dropped. */
export function ssoErrorOf(value: unknown): SsoError | undefined {
  return SSO_ERRORS.find((word) => word === value);
}

/** A ticket is URL-safe base64, at most 128 characters; anything else is not one. */
export function ssoTicketOf(value: unknown): string | undefined {
  return typeof value === "string" && /^[A-Za-z0-9_-]{16,128}$/.test(value) ? value : undefined;
}
