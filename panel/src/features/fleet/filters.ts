/**
 * The fleet's filters, in the URL so a filtered view can be shared: `?q=shop&server=web-2&state=failed`.
 * `server`, not `node`: the router keeps `node` for the server a page is on, and the fleet's
 * pages are on none.
 */

export interface FleetSearch {
  /** Text to find in a row's name, its server or its labels. */
  q?: string;
  /** Only this server's rows. */
  server?: string;
  /** A state, in each view's own words (`failed`, `expiring`, `none`...). */
  state?: string;
}

function text(value: unknown): string | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

/** A change to the filters: a key set to undefined is cleared. */
export type FleetSearchPatch = { [K in keyof FleetSearch]?: FleetSearch[K] | undefined };

/** The filters with a change applied, without the keys cleared. */
export function patchSearch(search: FleetSearch, patch: FleetSearchPatch): FleetSearch {
  const next: FleetSearchPatch = { ...search, ...patch };
  const clean: FleetSearch = {};
  if (next.q !== undefined && next.q !== "") clean.q = next.q;
  if (next.server !== undefined) clean.server = next.server;
  if (next.state !== undefined) clean.state = next.state;
  return clean;
}

/** A route's `validateSearch` for every fleet view. */
export function validateFleetSearch(search: Record<string, unknown>): FleetSearch {
  const out: FleetSearch = {};
  const q = text(search["q"]);
  const server = text(search["server"]);
  const state = text(search["state"]);
  if (q !== undefined) out.q = q;
  if (server !== undefined) out.server = server;
  if (state !== undefined) out.state = state;
  return out;
}

export function isFiltered(search: FleetSearch): boolean {
  return search.q !== undefined || search.server !== undefined || search.state !== undefined;
}

/** Whether a row's words contain what was typed, case and accents aside. */
export function matchesQuery(words: readonly (string | null | undefined)[], query: string | undefined): boolean {
  if (query === undefined || query.trim() === "") return true;
  const fold = (value: string): string => value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
  const wanted = fold(query.trim());
  return words.some((word) => word !== null && word !== undefined && fold(word).includes(wanted));
}
