/**
 * The access token, kept in this page's memory for a moment, for one use: the first administrator.
 *
 * Creating an account asks to "Confirm it's you" with the same token that signed in a few seconds
 * ago, and on a server with nothing on it that is the only proof there is. Asking for it twice in
 * a minute proves nothing the first time did not, so the sign-in that leads straight to the first
 * administrator hands the token to that page, which gives it to the confirmation once, unasked.
 *
 * Never stored (not in storage, not in a cookie, not in the address), never sent anywhere but to
 * the confirmation it is for, gone after one use, after two minutes, or as soon as the sign-in goes
 * anywhere else. A page that is reloaded has forgotten it, and the ordinary dialog asks.
 */

/** How long the token is kept: the time to fill in one form. */
export const ACCESS_PROOF_MS = 120_000;

let held: { token: string; until: number } | null = null;

/** Keeps the token that just signed in, for the confirmation of the first administrator. */
export function holdAccessToken(token: string, now: number = Date.now()): void {
  held = token === "" ? null : { token, until: now + ACCESS_PROOF_MS };
}

/** The token held, once: it is forgotten as it is handed over. Null when none, or when its time has passed. */
export function takeAccessToken(now: number = Date.now()): string | null {
  const current = held;
  held = null;
  return current !== null && current.until > now ? current.token : null;
}

/** Forgets the token: the sign-in did not lead where it was for. */
export function forgetAccessToken(): void {
  held = null;
}
