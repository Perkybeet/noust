/** The three views of a site's configuration, and how the URL names them (`?view=`). */

export const SITE_VIEWS = ["text", "structure", "diagram"] as const;

export type SiteView = (typeof SITE_VIEWS)[number];

export interface SiteSearch {
  /** Absent for the text, the view every site has. */
  view?: Exclude<SiteView, "text">;
}

/** Keeps `view` when it names a view other than the text; anything else is the text. */
export function validateSiteSearch(search: Record<string, unknown>): SiteSearch {
  const view = search["view"];
  return view === "structure" || view === "diagram" ? { view } : {};
}

/** Whether Noust can show a web server's sites as a structure: the two it administers. */
export function hasStructure(webserver: string): boolean {
  return webserver === "nginx" || webserver === "apache";
}
