/**
 * How a page writes its own search params (a chart range, a filter, a view) through the
 * router: in place.
 *
 * The router treats every navigation as a new page and scrolls it to the top. A page changing
 * what it shows is not a new page: the operator who picked "7 days" under the charts, or a
 * filter above a long table, must stay where they are. So every search-only navigation of a
 * route spreads `inPlace()` (src/app/searchNavigation.test.ts holds every route to it), and
 * `LinkTabs` keeps the position the same way when a section changes.
 */

export interface InPlaceOptions {
  /**
   * Replace the history entry instead of adding one: a view toggle, a range, a keystroke in a
   * search box. Back then leaves the page rather than stepping through every value it had.
   */
  replace?: boolean | undefined;
}

/** The navigation options of a search-only change: never scrolls, replaces when asked. */
export function inPlace(options?: InPlaceOptions): { resetScroll: false; replace: boolean } {
  return { resetScroll: false, replace: options?.replace ?? false };
}
