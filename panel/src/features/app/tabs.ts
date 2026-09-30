/**
 * The sections of an application's page, as the strip under its header draws them.
 *
 * The list itself is `APP_TABS` in app/nav.ts, the console's one register of destinations (the
 * command palette reads it too). To add a section - the app's database, say - add its route
 * under routes/_console/apps/$domain/, its label under `nav.appTabs` in the nav catalogs, and
 * one entry to `APP_TABS` before Settings, which stays last. A section that only some apps have
 * is left out here, in `appTabs`, from what the app says about itself. Eight sections at most
 * (docs/DESIGN.md, "LinkTabs"): Diagnose keeps its route off the strip, linked from the status
 * banner and the header's "More actions".
 */

import { APP_TABS } from "../../app/nav";
import type { LinkTab } from "../../app/LinkTabs";

/** Room a count keeps while it loads (`null`), per section path, when a section has one. */
export type SectionCounts = Readonly<Partial<Record<string, number | null>>>;

/**
 * The tabs of one app: every section, its domain filled in, and a count after the label where
 * one is given.
 */
export function appTabs(domain: string, counts: SectionCounts = {}): LinkTab[] {
  return APP_TABS.map((tab) => {
    const count = counts[tab.to];
    return { ...tab, params: { domain }, ...(count !== undefined ? { count } : {}) };
  });
}
