/**
 * Which page of the documentation explains the page of the console on screen (checklist N-18).
 *
 * The table is `help-links.json`, beside this file, so that the test which resolves every entry
 * against the documentation's own pages and headings (tests/test_help_links.py) reads the very
 * data the console ships. A route is a path of the console with `$params` for its variable parts;
 * the longest entry that is a prefix of the path wins, so a page nobody wrote an entry for still
 * gets the one of the area it is in.
 */

import table from "./help-links.json";

export interface HelpLink {
  /** The documentation site's route for the page (docs/site.yaml). */
  slug: string;
  /** The Markdown file in the repository. */
  source: string;
  /** A heading of that page, without the `#`. */
  anchor?: string;
}

interface Entry extends HelpLink {
  route: string;
}

const REPOSITORY = "https://github.com/Perkybeet/noust";
const BRANCH = "main";

/**
 * Where the documentation is read. The Markdown is rendered by GitHub until a documentation site
 * is published (`scripts/build_docs.py` builds it); then this is the one line that changes, to the
 * site's address, and every link in the console follows.
 */
export const DOCS_SITE: string | null = null;

const ENTRIES: readonly Entry[] = table.pages;
const DEFAULT_LINK: HelpLink = table.default;

function segments(path: string): string[] {
  return path.split("/").filter((segment) => segment !== "");
}

/** How well a route matches a path: -1 when it does not, else the number of segments it covers. */
function match(route: string, path: readonly string[]): number {
  const wanted = segments(route);
  if (wanted.length > path.length) return -1;
  for (let index = 0; index < wanted.length; index += 1) {
    const segment = wanted[index];
    if (segment === undefined) return -1;
    if (!segment.startsWith("$") && segment !== path[index]) return -1;
  }
  return wanted.length;
}

/**
 * The page of the documentation for a path of the console.
 *
 * @param pathname - The path on screen, such as `/apps/shop.example.com/settings/build`. A fleet
 * server's pages (`?node=`) share their path with this server's, so the query is not needed.
 * @returns The entry for the most specific route that is a prefix of the path; the documentation
 * of the console itself when none is.
 */
export function helpFor(pathname: string): HelpLink {
  const path = segments(pathname);
  let best: Entry | null = null;
  let bestScore = -1;
  for (const entry of ENTRIES) {
    const score = match(entry.route, path);
    if (score > bestScore) {
      best = entry;
      bestScore = score;
    }
  }
  return best ?? DEFAULT_LINK;
}

/**
 * The address a help link opens.
 *
 * @param link - An entry of the table.
 * @returns The page on the documentation site when there is one, the Markdown on GitHub until then.
 */
export function helpUrl(link: HelpLink): string {
  const anchor = link.anchor === undefined ? "" : `#${link.anchor}`;
  if (DOCS_SITE !== null) return `${DOCS_SITE.replace(/\/$/, "")}/${link.slug}/${anchor}`;
  return `${REPOSITORY}/blob/${BRANCH}/${link.source}${anchor}`;
}

/** Every route the table names, for the tests that check the table against the console's routes. */
export function helpRoutes(): readonly string[] {
  return ENTRIES.map((entry) => entry.route);
}
