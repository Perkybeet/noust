/**
 * What this browser was told "later" about, and when it is offered again (item 158).
 *
 * Two offers can be put off at the door of a new server: the first account ("Not now" on the page that
 * creates it) and an account's second factor ("Set up later"). Before this, the first was made again at every
 * sign-in with the access token, which for an agency that shares the token is a notice every day, and the
 * second was never mentioned again except by a reminder on Home. Now each is put off with the moment it was
 * and comes back once, a week later, on the next sign-in; after that only the places that already say it
 * (Home's list, the account's page, the list of accounts) do.
 *
 * It is the browser's, kept the way the fold of the main bar is (app/preferences.ts): nothing is stored on the
 * server, because a person who says "not now" is deciding for the place they are in, and a server that has no
 * accounts has no one to keep it for. A browser that keeps nothing makes the offer every time and never throws.
 */

/** How long an offer put off stays put off: a week. */
export const RETURNS_AFTER_MS = 7 * 24 * 60 * 60 * 1000;

/** The one key all of it is kept under: a map from the offer to what is known of it. */
export const LATER_KEY = "noust.later";

/** The offers that can be put off: the first account, or the second factor of one account (by its username). */
export type Offer = "account" | `factor:${string}`;

/** The second factor of one account, which another account on the same browser does not share. */
export function factorOffer(username: string): Offer {
  return `factor:${username}`;
}

interface Entry {
  /** When it was put off, in milliseconds since the epoch. */
  at: number;
  /** It was already offered again once: it is not offered a third time. */
  returned: boolean;
}

/** What a browser knows of an offer: never put off, put off and waiting, put off and due to return, or returned once already. */
export type Standing = "new" | "waiting" | "due" | "spent";

function isEntry(value: unknown): value is Entry {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return typeof entry["at"] === "number" && Number.isFinite(entry["at"]) && typeof entry["returned"] === "boolean";
}

/** Every entry the browser holds, each one checked: whatever else is stored under the key is as if it were not. */
function readAll(): Record<string, Entry> {
  try {
    const raw = window.localStorage.getItem(LATER_KEY);
    if (raw === null) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const entries: Record<string, Entry> = {};
    for (const [key, value] of Object.entries(parsed)) if (isEntry(value)) entries[key] = value;
    return entries;
  } catch {
    // Storage disabled (privacy modes, some embedded browsers) or damaged: nothing was put off.
    return {};
  }
}

function writeAll(entries: Record<string, Entry>): void {
  try {
    window.localStorage.setItem(LATER_KEY, JSON.stringify(entries));
  } catch {
    // Not persisted: the offer is made again, which is the safe side of forgetting.
  }
}

function entryOf(offer: Offer): Entry | undefined {
  const entries = readAll();
  return Object.hasOwn(entries, offer) ? entries[offer] : undefined;
}

/** Where an offer stands now. */
export function standing(offer: Offer, now: number = Date.now()): Standing {
  const entry = entryOf(offer);
  if (entry === undefined) return "new";
  if (entry.returned) return "spent";
  return now - entry.at >= RETURNS_AFTER_MS ? "due" : "waiting";
}

/**
 * The person said "later": it is not offered again until a week has passed. Putting it off on its return keeps
 * it spent, so it never comes back a third time.
 */
export function putOff(offer: Offer, now: number = Date.now()): void {
  const entries = readAll();
  const previous = Object.hasOwn(entries, offer) ? entries[offer] : undefined;
  entries[offer] = { at: now, returned: previous?.returned ?? false };
  writeAll(entries);
}

/** Spends the one return an offer has, as it is made. */
function spend(offer: Offer): void {
  const entries = readAll();
  const entry = Object.hasOwn(entries, offer) ? entries[offer] : undefined;
  if (entry === undefined) return;
  entries[offer] = { ...entry, returned: true };
  writeAll(entries);
}

/**
 * Whether an offer that starts as made (the first account) is made now: when it was never put off, every time, and
 * when it was, once, a week later. Making the return spends it.
 */
export function offerTo(offer: Offer, now: number = Date.now()): boolean {
  const where = standing(offer, now);
  if (where === "new") return true;
  if (where !== "due") return false;
  spend(offer);
  return true;
}

/**
 * Whether an offer that only returns (a second factor somebody put off) is made now: once, a week after it was put
 * off. One that was never put off is not made at all: nobody said "later" to it, so there is nothing to bring back.
 */
export function returnOf(offer: Offer, now: number = Date.now()): boolean {
  if (standing(offer, now) !== "due") return false;
  spend(offer);
  return true;
}
