/**
 * The sections of an application's page, as the strip under its header draws them.
 *
 * The list itself is `APP_TABS`, read from the application's group of the console's one register of
 * destinations (features/nav/app.ts; the register is app/nav.ts, and the command palette reads it
 * too). It is imported from the group, not the register, so this page loads only its own texts. To
 * add a section - the app's database, say - add its route under routes/_console/apps/$domain/, its
 * label under `nav.appTabs` in the nav catalogs, and one entry to `APP_SCREENS` before Settings,
 * which stays last. A section that only some apps have
 * is left out here, in `appTabs`, from what the app says about itself. Eight sections at most
 * (docs/DESIGN.md, "LinkTabs"): Diagnose keeps its route off the strip, linked from the status
 * banner and the header's "More actions".
 */

import { APP_TABS } from "../nav/app";
import type { LinkTab } from "../../app/LinkTabs";

/** Room a count keeps while it loads (`null`), per section path, when a section has one. */
export type SectionCounts = Readonly<Partial<Record<string, number | null>>>;

/**
 * What an adopted site has to show: its state, its logs and its use of the machine. It has no
 * deployments to list, no environment Noust writes, no database it links and no domains it
 * manages (checklist B-85).
 */
const ADOPTED_TABS: ReadonlySet<string> = new Set(["/apps/$domain", "/apps/$domain/logs", "/apps/$domain/metrics", "/apps/$domain/settings"]);

/**
 * The tabs of one app: every section, its domain filled in, and a count after the label where
 * one is given.
 */
export function appTabs(domain: string, counts: SectionCounts = {}, options: { adopted?: boolean } = {}): LinkTab[] {
  return APP_TABS.filter((tab) => options.adopted !== true || ADOPTED_TABS.has(tab.to)).map((tab) => {
    const count = counts[tab.to];
    return { ...tab, params: { domain }, ...(count !== undefined ? { count } : {}) };
  });
}
