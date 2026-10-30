/** The search params of one zone's page, apart from the page so the route's `validateSearch` does not pull it in. */

export const ZONE_VIEWS = ["records", "dnssec", "delegation"] as const;
export type ZoneView = (typeof ZONE_VIEWS)[number];

export interface ZoneSearch {
  /** Which of the zone's three sections is open; the records when there is none. */
  view?: ZoneView;
}

export function validateZoneSearch(search: Record<string, unknown>): ZoneSearch {
  const view = ZONE_VIEWS.find((candidate) => candidate === search["view"]);
  return view === undefined || view === "records" ? {} : { view };
}
