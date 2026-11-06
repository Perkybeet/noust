/**
 * The search params of the pages of Network, apart from the pages themselves so a route's `validateSearch` does not
 * pull in a whole page (importing anything from a module runs the whole of it, heavy imports included).
 */

export interface NewSearch {
  /** Arriving with `?new` (the shortcut of Home, the palette): the dialog that opens what the page holds opens, and the link is spent. */
  new?: true;
}

/** Reads `?new`, dropping anything else. */
export function validateNewSearch(search: Record<string, unknown>): NewSearch {
  return search["new"] === true || search["new"] === "true" || search["new"] === 1 ? { new: true } : {};
}
