/**
 * The last things the operator opened from the command palette, kept in this browser so that an
 * empty palette offers them (NV-14): the ids of commands, three at most, the newest first. An id
 * that the palette no longer offers (an application that was deleted) is dropped where it is read.
 */

const KEY = "noust.palette.recent";

/** How many are kept; the palette shows as many. */
export const RECENT_KEPT = 3;

/** The ids kept, newest first. Anything that is not a list of text is nothing. */
export function readRecent(): string[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string").slice(0, RECENT_KEPT) : [];
  } catch {
    // Storage disabled or something else wrote there: nothing is remembered, and the palette is as it always was.
    return [];
  }
}

/** Puts a command first among those remembered. */
export function rememberRecent(id: string): void {
  try {
    const kept = [id, ...readRecent().filter((known) => known !== id)].slice(0, RECENT_KEPT);
    window.localStorage.setItem(KEY, JSON.stringify(kept));
  } catch {
    // Not kept past this visit; the palette still works.
  }
}
