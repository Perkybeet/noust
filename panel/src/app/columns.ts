/**
 * What the second column of the frame holds: the sections of the area, or of the resource, a page is under,
 * as the register says them (`app/nav.ts`). Every area has one (docs/DESIGN.md, NV-22): the sections of an
 * area that has several pages, the fixed views of one that has a single page (`features/nav/views.ts`),
 * and, where a person enters a resource (an application, a database, a service, a team), the sections
 * of that resource, which the column slides to with the way back to the area. The column is the only
 * place a section is listed as a list: the strip of tabs of a page only ever changes the view of one
 * thing (NV-23), and a phone draws the same list as a sheet behind a selector at the top of the page.
 *
 * Nothing here draws: the column, the sheet and the tests read these derivations, so the three can never
 * list different sections.
 */

import type { LucideIcon } from "lucide-react";

import { ADOPTED_TABS } from "../features/app/tabs";
import { columnIconOf } from "../features/nav/icons";
import { VIEWS } from "../features/nav/views";
import type { PlainKey } from "../i18n";
import { DESTS, areaOf, dest, fleetTarget, isUnder, rootOf, rootOffered } from "./nav";
import { shown } from "./navCore";
import type { ConsolePath, Dest, Intent, NavContext, Scope } from "./navCore";

/** The areas that have a column (all of them, the fleet's faces included), and the resources whose sections replace it. */
export type ColumnKind =
  | "overview"
  | "apps"
  | "catalog"
  | "databases"
  | "cron"
  | "domains"
  | "network"
  | "logs"
  | "alerts"
  | "activity"
  | "backups"
  | "security"
  | "server"
  | "settings"
  | "account"
  | "app"
  | "database"
  | "service"
  | "team";

/** The resources a column slides in to (depth 1): each has the area it is entered from. */
export const RESOURCE_PARENT = {
  app: "apps",
  database: "databases",
  service: "server",
  team: "apps",
} as const satisfies Partial<Record<ColumnKind, ColumnKind>>;

export type ResourceKind = keyof typeof RESOURCE_PARENT;

/** How deep a column is: an area's is 0 and a resource's 1. The slide goes the way the depth does. */
export function depthOf(kind: ColumnKind): 0 | 1 {
  return kind in RESOURCE_PARENT ? 1 : 0;
}

const APP_PAGE = /^\/apps\/(?!new(?:\/|$))[^/]+(?:\/|$)/;
/** A database's own page, `/databases/$engine/$name`: not the servers' section nor one server's settings. */
const DATABASE_PAGE = /^\/databases\/(?!servers(?:\/|$)|engines(?:\/|$))[^/]+\/[^/]+(?:\/|$)/;
const SERVICE_PAGE = /^\/server\/services\/[^/]+(?:\/|$)/;
const TEAM_PAGE = /^\/teams\/[^/]+(?:\/|$)/;

/**
 * The column a path has: the resource's where one is entered, and otherwise its area's. Null only for a
 * path that is under no area (and so no page: a 404, the sign-in).
 */
export function columnKindOf(pathname: string): ColumnKind | null {
  if (APP_PAGE.test(pathname)) return "app";
  if (DATABASE_PAGE.test(pathname)) return "database";
  if (SERVICE_PAGE.test(pathname)) return "service";
  if (TEAM_PAGE.test(pathname)) return "team";
  if (isUnder(pathname, "/account")) return "account";
  const area = areaOf(pathname);
  return area === undefined ? null : (area.id as ColumnKind);
}

/** One section in the column, with what the register says about it. */
export interface ColumnItem {
  id: string;
  label: PlainKey;
  /** The route's own path, parameters included (`/apps/$domain/logs`); whoever draws the link fills them in. */
  to: ConsolePath;
  /** What a view of a list sets in the address (`/logs?source=system`): it is part of which one is current. */
  search?: Readonly<Record<string, string>>;
  /** Current only on exactly this path, not beneath it. */
  exact: boolean;
  icon: LucideIcon;
}

/** Sections that go together, under a title where the register gives them one. */
export interface ColumnGroup {
  id: string;
  /** The catalog key of the title: absent for a list that is one group. */
  heading?: PlainKey;
  danger?: boolean;
  items: ColumnItem[];
}

/** The order the groups of an application are drawn in: use it, configure it, fix it, move it, and what is dangerous last. */
export const INTENT_ORDER: readonly Intent[] = ["use", "configure", "fix", "move", "danger"];

/** The titles of the groups an area's column draws, by the `column` of its entries. */
export const COLUMN_GROUP_TITLES: Readonly<Record<string, PlainKey>> = {
  publish: "nav.columnGroups.publish",
  machine: "nav.columnGroups.machine",
  running: "nav.columnGroups.running",
  docker: "nav.columnGroups.docker",
  tools: "nav.columnGroups.tools",
  fleet: "nav.columnGroups.fleet",
  dns: "nav.columnGroups.dns",
};

/**
 * What a section is called from All servers: the register says it where the fleet's page of a section is not the section of one server (`fleetLabel`:
 * "Servers" for the list of them, which with a server is "Status"; "With an update" for the servers that wait for one). Everywhere else the name is the section's.
 */
function nameOf(entry: Dest, first: boolean, scope: Scope): PlainKey {
  const own = first ? (entry.rootLabel ?? entry.label) : entry.label;
  return scope === "fleet" ? (entry.fleetLabel ?? own) : own;
}

function itemOf(entry: Dest, first: boolean, scope: Scope = "server"): ColumnItem {
  return {
    id: entry.id,
    label: nameOf(entry, first, scope),
    // All servers gives each section the address it has under /fleet, so a click stays in the scope (item 196).
    to: scope === "fleet" && entry.scope !== "fleet" ? fleetTarget(entry) : entry.to,
    exact: first || entry.exact === true,
    icon: columnIconOf(entry),
  };
}

/**
 * What an adopted site (one Noust did not build) has to show: its state, its logs and its use of the
 * machine, and the way to stop managing it. It has no deployments to list, no environment Noust writes,
 * no database it links, no domains it manages and no code to build (checklist B-85), and nothing to export.
 */
function suitsAdopted(entry: Dest): boolean {
  return ADOPTED_TABS.has(entry.to) || entry.intent === "fix";
}

/**
 * An application's sections in groups by intent: the register's own entries (`intent`), those a
 * principal may see and that suit the application. The order inside a group is the register's.
 */
export function appColumn(ctx: NavContext, adopted: boolean): ColumnGroup[] {
  const entries = DESTS.filter((entry) => (entry.id === "app" || entry.id.startsWith("app.")) && entry.intent !== undefined && shown(entry, ctx) && (!adopted || suitsAdopted(entry)));
  return INTENT_ORDER.flatMap((intent) => {
    const items = entries.filter((entry) => entry.intent === intent).map((entry) => itemOf(entry, false));
    return items.length > 0 ? [{ id: intent, heading: `nav.intents.${intent}` as PlainKey, ...(intent === "danger" ? { danger: true } : {}), items }] : [];
  });
}

/**
 * A database's sections are grouped by what each is for, in the words that fit a database: what you look at,
 * what you set up, and what is dangerous. A group the engine answers nothing of is absent (Mongo has no query
 * and no metrics: its look-at group is its overview), and a group of one is as good as one of four.
 */
const DATABASE_GROUPS: readonly { intent: Intent; heading: PlainKey; danger?: true }[] = [
  { intent: "use", heading: "nav.dbIntents.use" },
  { intent: "configure", heading: "nav.dbIntents.configure" },
  { intent: "danger", heading: "nav.intents.danger", danger: true },
];

/** What a resource other than an application is made of: its own page and its tabs, in the register's order. */
export function resourceColumn(kind: Exclude<ResourceKind, "app">, ctx: NavContext): ColumnGroup[] {
  const root = { database: "database", service: "service", team: "team" }[kind];
  const entries = DESTS.filter((entry) => (entry.id === root || entry.parent === root) && (entry.kind === "tab" || entry.id === root) && shown(entry, ctx));
  if (kind === "database") {
    return DATABASE_GROUPS.flatMap((group) => {
      const items = entries.filter((entry) => entry.intent === group.intent).map((entry) => itemOf(entry, entry.id === root));
      return items.length > 0 ? [{ id: group.intent, heading: group.heading, ...(group.danger === true ? { danger: true } : {}), items }] : [];
    });
  }
  return [{ id: "all", items: entries.map((entry, index) => itemOf(entry, index === 0)) }];
}

/** The person's own account: its page, and the security of their sign-in. */
export function accountColumn(ctx: NavContext): ColumnGroup[] {
  const entries = DESTS.filter((entry) => (entry.id === "account" || entry.parent === "account") && shown(entry, ctx));
  return [{ id: "all", items: entries.map((entry, index) => itemOf(entry, index === 0)) }];
}

/**
 * The entries of an area's column: its own page and its tabs, and what a hidden screen asks to be in (`column`: Terminal).
 * They are the same with a server chosen and with All servers (item 196): a person who changes the selector finds the same
 * sections, each with its fleet page or, meanwhile, the table of servers that opens each. The sections only the fleet has
 * (the applications that run on several servers, the copies the central holds) are added after them in the fleet's.
 * What a server cannot do (`ctx.features`) is absent; the fleet is not judged by the central's own machine.
 */
function areaEntries(area: Dest, ctx: NavContext, scope: Scope): Dest[] {
  const inArea = (entry: Dest): boolean => entry !== area && rootOf(entry) === area && !entry.to.includes("$") && shown(entry, ctx);
  const own = [...(rootOffered(area, ctx) ? [area] : []), ...DESTS.filter((entry) => entry.scope !== "fleet" && (entry.kind === "tab" || entry.column !== undefined) && inArea(entry))];
  if (scope !== "fleet") return own;
  // A fleet-only section that is the face of a section already listed (Updates, Software) is that section.
  const faces = new Set(own.map((entry) => fleetTarget(entry)));
  return [...own, ...DESTS.filter((entry) => entry.scope === "fleet" && entry.kind === "tab" && inArea(entry) && !faces.has(entry.to))];
}

/**
 * The column of an area: its sections in the groups the register gives them, then the fixed views of an area that is
 * a single page. A group is its `column`; entries that name none are the list's head. The sections are the same with
 * both scopes (item 196), so the second column never shows fewer with All servers than with a server.
 */
export function areaColumn(kind: ColumnKind, ctx: NavContext, scope: Scope = "server"): ColumnGroup[] {
  const area = dest(kind);
  const entries = areaEntries(area, ctx, scope);
  const groups: ColumnGroup[] = [];
  for (const entry of entries) {
    const id = entry.column ?? "";
    let group = groups.find((candidate) => candidate.id === id);
    if (group === undefined) {
      const heading = COLUMN_GROUP_TITLES[id];
      group = { id, ...(heading !== undefined ? { heading } : {}), items: [] };
      groups.push(group);
    }
    group.items.push(itemOf(entry, entry === area, scope));
  }
  const views = (VIEWS[kind] ?? []).filter((view) => view.feature === undefined || ctx.features === undefined || ctx.features(view.feature));
  if (views.length > 0) {
    const head = groups.find((group) => group.id === "");
    const to = scope === "fleet" ? fleetTarget(area) : area.to;
    const items = views.map((view): ColumnItem => ({ id: `${kind}.view.${view.id}`, label: view.label, to, ...(view.search !== undefined ? { search: view.search } : {}), exact: true, icon: columnIconOf({ id: `${kind}.view.${view.id}` }) }));
    // The area's own page is the first view, "All": the views come in its place, not after it.
    if (head !== undefined) head.items.splice(0, head.items.length, ...items, ...head.items.filter((item) => item.to !== to));
    else groups.unshift({ id: "", items });
  }
  return groups;
}

/** The areas whose column is the register's list of their sections (the others draw a body of their own: Home's panels, Settings, a resource's). */
const LISTED: ReadonlySet<ColumnKind> = new Set(["apps", "catalog", "databases", "cron", "domains", "network", "logs", "alerts", "activity", "backups", "security"]);

/**
 * Whether a column has anything to list. An area none of whose sections the server can do has no column at all: it is not drawn as a head over
 * an empty list (the page itself says why it is not there, `FeatureGate`).
 */
export function hasSections(kind: ColumnKind, ctx: NavContext, scope: Scope): boolean {
  return !LISTED.has(kind) || areaColumn(kind, ctx, scope).length > 0;
}

/**
 * The item of a list that a page is in: the one whose path is the longest start of the page's route,
 * so a section beneath another (an application's Export, under its Settings) is the only one current.
 * `route` is the matched route's own path, parameters included, as `fullPath` gives it. Of items that
 * share the path (a list's views), the one whose search is what the address says wins, and the one
 * with none is current when no other matches.
 */
export function currentItem<T extends { to: string; exact?: boolean; search?: Readonly<Record<string, unknown>> }>(
  items: readonly T[],
  route: string,
  search: Readonly<Record<string, unknown>> = {},
): T | undefined {
  const path = route.length > 1 ? route.replace(/\/+$/, "") : route;
  let best: T | undefined;
  const better = (item: T): boolean => {
    if (best === undefined) return true;
    if (item.to.length !== best.to.length) return item.to.length > best.to.length;
    // The same path: a view that the address matches beats the list's plain head.
    return item.search !== undefined && best.search === undefined;
  };
  for (const item of items) {
    const under = item.exact === true ? path === item.to : path === item.to || path.startsWith(`${item.to}/`);
    if (!under) continue;
    if (item.search !== undefined && !Object.entries(item.search).every(([key, value]) => search[key] === value)) continue;
    if (better(item)) best = item;
  }
  return best;
}
