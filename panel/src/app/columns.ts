/**
 * What the second column of the navigation holds: the sections of the resource a page is under, as the
 * register says them (`app/nav.ts`), grouped by what they are for. The column is one presentation of
 * them and the tab strip under the header of one bar is the other, so both read this and the two can
 * never list different things (docs/DESIGN.md, NV-1).
 *
 * Only a resource with sections has a column: an application, a server and Settings. A list has one
 * where it has views to put in it: Databases, with its two sections and the engines that filter them.
 * Home has none, because its fleet is a filter and each server leads to its page.
 */

import type { LucideIcon } from "lucide-react";

import { ADOPTED_TABS } from "../features/app/tabs";
import { columnIconOf } from "../features/nav/icons";
import type { PlainKey } from "../i18n";
import { DESTS, isUnder } from "./nav";
import { shown } from "./navCore";
import type { ConsolePath, Dest, Intent, NavContext } from "./navCore";

/** Which resource's sections the column holds. `databases` is a list with views to put in it: its two sections and its engines. */
export type ColumnKind = "app" | "server" | "settings" | "databases";

const APP_PAGE = /^\/apps\/(?!new(?:\/|$))[^/]+(?:\/|$)/;

/** The pages of Databases that have the column: the list, the database servers and one server's settings; a database's own page has its tabs. */
const DATABASES_PAGE = /^\/databases(?:\/servers|\/engines\/[^/]+\/settings)?\/?$/;

/** The column a path has, or none: an application's page, the server's, Databases, or one of Settings. */
export function columnKindOf(pathname: string): ColumnKind | null {
  if (APP_PAGE.test(pathname)) return "app";
  if (isUnder(pathname, "/server")) return "server";
  if (isUnder(pathname, "/settings")) return "settings";
  if (DATABASES_PAGE.test(pathname)) return "databases";
  return null;
}

/** One section in the column, with what the register says about it. */
export interface ColumnItem {
  id: string;
  label: PlainKey;
  /** The route's own path, parameters included (`/apps/$domain/logs`); whoever draws the link fills them in. */
  to: ConsolePath;
  /** Current only on exactly this path, not beneath it. */
  exact: boolean;
  icon: LucideIcon;
}

/** Sections that go together. Only an application's are told apart by a title: a server's are one list. */
export interface ColumnGroup {
  id: Intent | "all";
  titled: boolean;
  items: ColumnItem[];
}

/** The order the groups of an application are drawn in: use it, configure it, fix it, and what is dangerous last. */
export const INTENT_ORDER: readonly Intent[] = ["use", "configure", "fix", "danger"];

function itemOf(entry: Dest): ColumnItem {
  return {
    id: entry.id,
    label: entry.id === "server" ? (entry.rootLabel ?? entry.label) : entry.label,
    to: entry.to,
    exact: entry.exact === true || entry.id === "server",
    icon: columnIconOf(entry),
  };
}

/**
 * What an adopted site (one Noust did not build) has to show: its state, its logs and its use of the
 * machine, and Delete. It has no deployments to list, no environment Noust writes, no database it links
 * and no domains it manages (checklist B-85), and nothing to export.
 */
function suitsAdopted(entry: Dest): boolean {
  return ADOPTED_TABS.has(entry.to) || entry.intent === "fix" || entry.id === "app.settings.delete";
}

/**
 * An application's sections in four groups by intent: the register's own entries (`intent`), those a
 * principal may see and that suit the application. The order inside a group is the register's.
 */
export function appColumn(ctx: NavContext, adopted: boolean): ColumnGroup[] {
  const entries = DESTS.filter((entry) => (entry.id === "app" || entry.id.startsWith("app.")) && entry.intent !== undefined && shown(entry, ctx) && (!adopted || suitsAdopted(entry)));
  return INTENT_ORDER.flatMap((intent) => {
    const items = entries.filter((entry) => entry.intent === intent).map(itemOf);
    return items.length > 0 ? [{ id: intent, titled: true, items }] : [];
  });
}

/** The machine's sections: the first is its overview, then its tabs, then Terminal. One list, no titles. */
export function serverColumn(ctx: NavContext): ColumnGroup[] {
  const entries = DESTS.filter(
    (entry) => (entry.id === "server" || entry.parent === "server") && (entry.kind === "area" || entry.kind === "tab" || entry.intent !== undefined) && shown(entry, ctx),
  );
  return [{ id: "all", titled: false, items: entries.map(itemOf) }];
}

/**
 * The item of a list that a page is in: the one whose path is the longest start of the page's route,
 * so a section beneath another (an application's Export, under its Settings) is the only one current.
 * `route` is the matched route's own path, parameters included, as `fullPath` gives it.
 */
export function currentItem<T extends { to: string; exact?: boolean }>(items: readonly T[], route: string): T | undefined {
  const path = route.length > 1 ? route.replace(/\/+$/, "") : route;
  let best: T | undefined;
  for (const item of items) {
    const under = item.exact === true ? path === item.to : path === item.to || path.startsWith(`${item.to}/`);
    if (under && (best === undefined || item.to.length > best.to.length)) best = item;
  }
  return best;
}
