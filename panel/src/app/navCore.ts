/**
 * The register's vocabulary and the little that a page needs to draw a strip of tabs: what an
 * entry is, how a group of entries is written, and how a group becomes tabs. No text and no
 * screen is here, so a page that draws its tabs imports this and the group it draws
 * (features/nav/backups.ts) and not the whole register (app/nav.ts), which the palette, the
 * sidebar and the tests read. docs/DESIGN.md, NV-1.
 */

import type { LucideIcon } from "lucide-react";

import type { PlainKey } from "../i18n";
import type { FileRouteTypes } from "../routeTree.gen";
import { INTERIM_ROWS } from "./redirects";

/** Every path of the console, as the router knows it. */
export type ConsolePath = FileRouteTypes["to"];

/** Whose sidebar a destination is in. "This central" uses the fleet's. */
export type Scope = "server" | "fleet";

/** Where a screen is drawn: the sidebar, a tab strip, a settings list, or only reached from somewhere. */
export type Kind = "area" | "tab" | "section" | "hidden";

/** Whose a setting is, the first question of NV-7. */
export type Owner = "account" | "resource" | "group" | "server" | "instance" | "fleet";

/** A condition under which a destination exists at all; one that does not apply is absent, not disabled (NV-12). */
export type When = "organised" | "fleet" | "compose" | "unit" | "built";

export interface Dest {
  /** "apps", "app.deployments", "settings.alerts": dots say what is under what. */
  id: string;
  /** The router's path, parameters included. */
  to: ConsolePath;
  kind: Kind;
  /** The area, tab or resource this one is under: the breadcrumbs and the tree. */
  parent?: string;
  scope: Scope | "both";
  /** The sidebar's group, or the settings group (an id, titled by whoever draws it). */
  group?: string;
  /** Settings only: whose the section is (NV-7). */
  owner?: Owner;
  /** One name, the same in the sidebar, the title, the tab and the palette (NV-5). */
  label: PlainKey;
  /** Its name as the first entry of the strip or the list it opens ("Overview" under an area). */
  rootLabel?: PlainKey;
  /** Its phrase in the palette where the label alone would not say what it is: "Alerts settings". */
  command?: PlainKey;
  /** Other words an operator might type for it, the old names of a renamed screen apart (RENAMED). */
  keywords?: PlainKey;
  icon?: LucideIcon;
  /** The two keys that open it, such as ["g", "a"]. */
  shortcut?: readonly string[];
  /** What the shortcut list calls the shortcut. */
  goTo?: PlainKey;
  /** The permission that shows it: whoever lacks it does not see it, in the palette either. */
  permission?: string;
  when?: When;
  /** Capabilities of the resource (a database's engine): shown when it has any of them. */
  needs?: readonly string[];
  /** Active only on this exact path, not on the paths beneath it. */
  exact?: true;
  /** The central's, whichever server is selected: a node refuses a central on it on purpose (nodeRoute.ts). */
  central?: true;
  /** A screen that is in no list says where it is reached from (NV-13). */
  reachedFrom?: string;
}

/** A group of screens written together: an area or a resource first, then what is under it. */
export type Strip = readonly Dest[];

/** Some of an entry's fields, for the helpers below to fill in the rest. */
export type More = Partial<Omit<Dest, "id" | "kind" | "parent" | "label">>;

/** Where a screen under another is: its parent's path and its name (`/settings` and `alerts` make `/settings/alerts`). */
function under(parent: Dest, name: string): ConsolePath {
  return `${parent.to === "/" ? "" : parent.to}/${name}` as ConsolePath;
}

// The helpers build plain data and touch nothing, so a group nobody imports is dropped from a
// build, and the order of the register is the order app/nav.ts lists the groups in.

/** A destination of the sidebar. */
export const area = /* @__NO_SIDE_EFFECTS__ */ (id: string, to: ConsolePath, label: PlainKey, icon: LucideIcon, more: More = {}): Dest => ({
  id,
  to,
  kind: "area",
  scope: "server",
  label,
  icon,
  ...more,
});

/** A tab of an area or a resource: its path is the parent's and its name. */
export const tab = /* @__NO_SIDE_EFFECTS__ */ (parent: Dest, name: string, label: PlainKey, more: More = {}): Dest => ({
  id: `${parent.id}.${name}`,
  to: under(parent, name),
  kind: "tab",
  parent: parent.id,
  scope: "server",
  label,
  ...more,
});

/** A section of a list on the side (Settings, an application's settings, the account). */
export const section = /* @__NO_SIDE_EFFECTS__ */ (parent: Dest, name: string, label: PlainKey, more: More = {}): Dest => ({
  id: `${parent.id}.${name}`,
  to: under(parent, name),
  kind: "section",
  parent: parent.id,
  scope: "both",
  label,
  ...more,
});

/** A screen reached from a page, never listed: the resource's own page, a wizard, Terminal. */
export const hidden = /* @__NO_SIDE_EFFECTS__ */ (id: string, to: ConsolePath, label: PlainKey, reachedFrom: string, more: More & { parent?: string } = {}): Dest => ({
  id,
  to,
  kind: "hidden",
  scope: "server",
  label,
  reachedFrom,
  ...more,
});

/** A root and what is under it. The root is the first tab of its own strip. */
export const strip = /* @__NO_SIDE_EFFECTS__ */ (root: Dest, children: (root: Dest) => readonly Dest[]): Strip => [root, ...children(root)];

/**
 * New routes whose page does not exist yet. Until it does, each opens the screen that holds the
 * same thing today, so the route is never an empty page and nothing is stranded.
 */
export const INTERIM = INTERIM_ROWS as readonly { from: ConsolePath; to: ConsolePath; after: string }[];

const STAGED: ReadonlySet<string> = new Set(INTERIM.map((row) => row.from));

/** Whether the screen is only a route so far: lists do not offer it until its page exists. */
export function isStaged(entry: Dest): boolean {
  return STAGED.has(entry.to);
}

/** What a list is drawn for: who is looking, and which optional parts apply. */
export interface NavContext {
  /** Whether the principal holds a permission. Absent: nothing is left out for lack of one. */
  can?: (permission: string) => boolean;
  /** Which conditional destinations apply. Absent: every one is offered. */
  when?: Partial<Record<When, boolean>>;
  /** What the resource can do (a database's engine): a tab with `needs` is offered when it has any. Absent: all. */
  capabilities?: readonly string[];
  /** Include the screens that are only a route so far; for the register's own tests. */
  staged?: boolean;
}

/** Whether a list drawn for this context offers the entry. */
export function shown(entry: Dest, ctx: NavContext): boolean {
  if (ctx.staged !== true && isStaged(entry)) return false;
  if (entry.permission !== undefined && ctx.can !== undefined && !ctx.can(entry.permission)) return false;
  if (entry.when !== undefined && ctx.when !== undefined && ctx.when[entry.when] !== true) return false;
  if (entry.needs !== undefined && ctx.capabilities !== undefined && !entry.needs.some((need) => ctx.capabilities?.includes(need) === true)) return false;
  return true;
}

export interface TabItem {
  label: PlainKey;
  to: ConsolePath;
  /** Active only on this exact path (the first tab), not on paths beneath it. */
  exact?: boolean;
  keywords?: PlainKey;
}

/** An entry as one tab of a strip; the first of a strip is named by `rootLabel` where the area has one. */
export function tabItem(entry: Dest, first: boolean): TabItem {
  return {
    label: first ? (entry.rootLabel ?? entry.label) : entry.label,
    to: entry.to,
    ...(first || entry.exact === true ? { exact: true } : {}),
    ...(!first && entry.keywords !== undefined ? { keywords: entry.keywords } : {}),
  };
}

/** The entries of a strip drawn as tabs: the area or resource itself, which is the first tab, then its tabs. */
export function stripEntries(group: Strip, ctx: NavContext): Dest[] {
  const [root, ...rest] = group;
  return root === undefined ? [] : [root, ...rest.filter((entry) => entry.kind === "tab" && shown(entry, ctx))];
}

/**
 * The tabs under the header of an area or a resource, in order. Parameters of the path
 * (`$domain`) and counts are the page's to add (`withCounts`). It takes the group of screens the
 * page draws (`BACKUPS_SCREENS`), not an id: the page names what it draws, and
 * `npm run i18n:routes` reads from that which texts the route needs.
 */
export function tabsOf(group: Strip, ctx: NavContext = {}): TabItem[] {
  return stripEntries(group, ctx).map((entry, index) => tabItem(entry, index === 0));
}

/**
 * Counts on the tabs they belong to, by path. A count that is `null` while it loads keeps its
 * room, so the strip does not change width when it arrives.
 */
export function withCounts<T extends TabItem>(tabs: readonly T[], counts: Readonly<Partial<Record<string, number | null>>>): (T & { count?: number | null })[] {
  return tabs.map((entry) => {
    const count = counts[entry.to];
    return count === undefined ? entry : { ...entry, count };
  });
}
