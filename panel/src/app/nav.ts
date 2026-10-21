/**
 * The console's register of screens, written once (docs/DESIGN.md, NV-1). Every screen of the
 * router has an entry, or a reason in EXCEPTIONS, and everything that lists destinations is
 * derived from the register: the sidebar (`sidebarFor`), the tab strips (`tabsOf`), the settings
 * navigation (`settingsGroups`), the breadcrumbs (`crumbsOf`), the `g` shortcuts (`shortcuts`)
 * and the command palette (`paletteEntries`). `app/nav.register.test.ts` holds it to the router in
 * both directions, so a page cannot be reachable from one place and missing from another.
 *
 * An entry says what a screen is drawn as today (`kind`, `group`, `shortcut`, the label keys) and
 * who it belongs to (`owner`, NV-7). Where 4.0 draws a screen somewhere else, that is a change of
 * data in this file, not of a component: the dated exceptions are listed in the register test.
 *
 * Labels and search words are catalog keys (i18n/en/nav.ts and the namespace of each screen), not
 * text: whoever renders an entry translates it with `t(entry.label)`, so every place that shows a
 * destination speaks the active language.
 *
 * Where the entries are written decides what a page loads. A group of screens is a file of
 * features/nav/ (BACKUPS_SCREENS): the page that draws those tabs imports it and app/navCore.ts
 * and nothing else, so the first load does not carry the whole register and the route is given
 * the texts of that group alone (`npm run i18n:routes`). This file lists the groups in the order
 * the lists draw them and holds what needs all of them at once: the sidebar, the settings list,
 * the breadcrumbs, the shortcuts and the palette. A page imports from here only what it cannot
 * get from its group.
 */

import type { LucideIcon } from "lucide-react";

import { ACCOUNT_SCREENS } from "../features/nav/account";
import { APP_SCREENS, APP_SETTINGS_SCREENS } from "../features/nav/app";
import { APPS_SCREENS } from "../features/nav/apps";
import { BACKUPS_SCREENS } from "../features/nav/backups";
import { CATALOG_SCREENS } from "../features/nav/catalog";
import { DATABASES_SCREENS, DATABASE_SCREENS } from "../features/nav/databases";
import { DOMAINS_SCREENS } from "../features/nav/domains";
import { FLEET_SCREENS } from "../features/nav/fleet";
import { ACTIVITY_SCREENS, CRON_SCREENS, LOGS_SCREENS } from "../features/nav/machine";
import { OVERVIEW_SCREENS } from "../features/nav/overview";
import { PROJECTS_SCREENS } from "../features/nav/projects";
import { SERVER_SCREENS, SERVICE_SCREENS } from "../features/nav/server";
import { SETTINGS_SCREENS } from "../features/nav/settings";
import { TEAM_SCREENS } from "../features/nav/team";
import type { PlainKey } from "../i18n";
import { REDIRECT_ROWS } from "./redirects";
import { shown, tabsOf } from "./navCore";
import type { ConsolePath, Dest, NavContext, Owner, Scope, TabItem } from "./navCore";

export { INTERIM, isStaged, tabsOf, withCounts } from "./navCore";
export type { ConsolePath, Dest, Kind, NavContext, Owner, Scope, Strip, TabItem, When } from "./navCore";
export { APP_TABS } from "../features/nav/app";
export { FLEET_TABS } from "../features/nav/fleet";
export type { FleetTabItem } from "../features/nav/fleet";
export { SETTINGS_TABS } from "../features/nav/settings";
export type { SettingsScope, SettingsTabItem } from "../features/nav/settings";
export { TEAM_TABS } from "../features/nav/team";

/** The register itself, filled in the order the lists draw the groups. */
const REGISTERED: Dest[] = [];
const BY_ID = new Map<string, Dest>();

/** Every screen, in the order its list draws it. */
export const DESTS: readonly Dest[] = REGISTERED;

// The server scope first, groups separated by space (on a fleet the sidebar heads them with the
// server's name), then what is under them, then the fleet, Settings and the account.
for (const group of [
  OVERVIEW_SCREENS,
  PROJECTS_SCREENS,
  APPS_SCREENS,
  CATALOG_SCREENS,
  DATABASES_SCREENS,
  DOMAINS_SCREENS,
  BACKUPS_SCREENS,
  SERVER_SCREENS,
  CRON_SCREENS,
  LOGS_SCREENS,
  ACTIVITY_SCREENS,
  SERVICE_SCREENS,
  DATABASE_SCREENS,
  APP_SCREENS,
  APP_SETTINGS_SCREENS,
  TEAM_SCREENS,
  FLEET_SCREENS,
  SETTINGS_SCREENS,
  ACCOUNT_SCREENS,
]) {
  for (const entry of group) {
    if (BY_ID.has(entry.id)) throw new Error(`app/nav.ts declares "${entry.id}" twice`);
    BY_ID.set(entry.id, entry);
    REGISTERED.push(entry);
  }
}

/** The entry with this id. A typo in an id is a bug, so an unknown one throws instead of returning nothing. */
export function dest(id: string): Dest {
  const found = BY_ID.get(id);
  if (found === undefined) throw new Error(`app/nav.ts has no destination "${id}"`);
  return found;
}

/**
 * Routes that are not destinations, each with the reason: the screens of signing in, what an
 * outside service sends the browser back to, and the page of one thing (a deployment, a recipe, a
 * site) that a list opens. The register's test fails on a route that is in neither place.
 */
export const EXCEPTIONS: Readonly<Record<string, string>> = {
  "/login": "access (T7): outside the console's navigation",
  "/setup": "access (T7): the first administrator",
  "/welcome": "access (T7): the page a new installation opens on",
  "/invite": "access (T7): accepting an invitation",
  "/reset-password": "access (T7): choosing a new password with the link in a mail",
  "/integrations/github/callback": "where GitHub sends the browser back after creating or installing the App",
  "/integrations/gitlab/callback": "where GitLab sends the browser back after authorising the application",
  "/apps/$domain/deployments/$id": "one deployment, opened from the Deployments list (L4)",
  "/catalog/$name": "one recipe, opened from the catalog",
  "/databases/engines/$engine/settings": "one engine's settings, opened from the Engines tab",
  "/domains/sites/$site": "one site's web server file, opened from the Sites tab",
  "/fleet/jobs/$id": "one fleet job, opened from the notice that started it",
  "/projects/$team/$project": "one project, opened from the Projects list",
  "/server/services/$name/unit": "a service's unit file, opened from the service's page",
};

/**
 * Screens the plan puts in the register whose route another lane creates. The lane that adds the
 * route moves the entry into the register in the same change; the register test fails when a
 * route appears without it, and when an entry lingers here after its route exists.
 */
export const PLANNED: readonly { id: string; to: string; lane: string }[] = [
];

/**
 * What the screens that changed their name were called before, as palette search words (NV-5):
 * an operator who knew "Console access" still finds it. Keys, so the old name is translated.
 */
export const RENAMED: Readonly<Record<string, readonly PlainKey[]>> = {
  domains: ["nav.renamed.domains"],
  "settings.notifications": ["nav.renamed.notifications"],
  "settings.alerts": ["nav.renamed.alerts"],
  "settings.console": ["nav.renamed.console"],
  "settings.about": ["nav.renamed.about"],
  "settings.access": ["nav.renamed.access"],
  "settings.compliance": ["nav.renamed.compliance"],
  "settings.probes": ["nav.renamed.probes"],
  "settings.log-shipping": ["nav.renamed.logShipping"],
  "account.security": ["nav.renamed.security"],
};

/** An old address and where its screen is now (2.9 of the navigation design). */
export interface Redirect {
  from: ConsolePath;
  to: ConsolePath;
  /** Search parameters the destination gets besides the ones the old address carried. */
  search?: Readonly<Record<string, string>>;
  /**
   * `live`: the old address redirects. `pending`: its screen is still where it was because the
   * screen at `to` does not hold everything yet; redirecting now would strand part of it. The
   * lane in `after` flips it, in app/redirects.ts and in the route file, when it does.
   */
  state: "live" | "pending";
  after?: string;
}

/**
 * The URLs that change in 4.0, each redirecting to its new place (docs/MIGRATING-4.0.md carries
 * this table as "where it is now"). The rows are data in app/redirects.ts, typed here: a path
 * that is not a route fails the build. `routes/redirects.test.tsx` walks it through the real router.
 */
export const REDIRECTS: readonly Redirect[] = REDIRECT_ROWS;

/** The sidebar of a scope: its areas, grouped, in order. Settings is pinned under them and is not here. */
export function sidebarFor(scope: Scope, ctx: NavContext = {}): Dest[][] {
  const groups = new Map<string, Dest[]>();
  for (const entry of REGISTERED) {
    if (entry.kind !== "area" || entry.scope !== scope || entry.group === "pinned" || !shown(entry, ctx)) continue;
    const key = entry.group ?? entry.id;
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  return [...groups.values()];
}

/** The same tabs for an id the caller has as text: tests and the palette, which draw nothing on a route. */
export function tabsById(id: string, ctx: NavContext = {}): TabItem[] {
  return tabsOf([dest(id), ...REGISTERED.filter((entry) => entry.parent === id)], ctx);
}

/** The block of Settings a section is in: the selected server's own, or the instance's. */
export type SettingsBlock = "server" | "instance";

/** The groups of each block, in the order they are drawn. The ids are titled by whoever draws them. */
export const SETTINGS_GROUPS: Readonly<Record<SettingsBlock, readonly string[]>> = {
  server: ["this-server", "monitoring", "developers"],
  instance: ["people", "governance", "fleet"],
};

const BLOCK_OWNERS: Readonly<Record<SettingsBlock, readonly Owner[]>> = { server: ["server"], instance: ["instance", "fleet"] };

/** The group of a section: General, which is Settings' own page, is the first of "This server". */
function settingsGroupOf(entry: Dest): string {
  return entry.id === "settings" ? "this-server" : entry.group === undefined || entry.group === "" ? "this-server" : entry.group;
}

function settingsEntries(ctx: NavContext): Dest[] {
  return REGISTERED.filter((entry) => (entry.id === "settings" || (entry.parent === "settings" && entry.kind === "section")) && shown(entry, ctx));
}

/**
 * The sections of Settings a block holds, in titled groups. The account's own sections are not
 * Settings (NV-7): they leave for /account, so no block holds them.
 */
export function settingsGroups(block: SettingsBlock, ctx: NavContext = {}): { id: string; sections: Dest[] }[] {
  const entries = settingsEntries(ctx).filter((entry) => entry.owner !== undefined && BLOCK_OWNERS[block].includes(entry.owner));
  return SETTINGS_GROUPS[block].flatMap((id) => {
    const sections = entries.filter((entry) => settingsGroupOf(entry) === id);
    return sections.length > 0 ? [{ id, sections }] : [];
  });
}

/** The entries above one, root first: where it sits in the tree. A resource's own page stays in the chain, for its name to replace. */
export function crumbsOf(id: string): Dest[] {
  const chain: Dest[] = [];
  for (let parent = dest(id).parent; parent !== undefined; parent = chain[0]?.parent) {
    // A cycle is a typo in the register; the test says so, and this does not loop for ever on it.
    if (chain.length > 8) break;
    chain.unshift(dest(parent));
  }
  return chain;
}

/** The `g` shortcuts, in the order the shortcut list shows them. */
export function shortcuts(): { id: string; keys: readonly string[]; description: PlainKey; to: ConsolePath }[] {
  return REGISTERED.flatMap((entry) => (entry.shortcut !== undefined && entry.goTo !== undefined ? [{ id: entry.id, keys: entry.shortcut, description: entry.goTo, to: entry.to }] : []));
}

export interface PaletteEntry {
  id: string;
  to: ConsolePath;
  /** What the palette lists it as: its label, its phrase, or its place in text. */
  text: string;
  /** Where it lives, in words: "Server › System updates". */
  path: string;
  /** Search words, the old names included. */
  keywords: string;
  shortcut?: readonly string[];
}

export interface PaletteContext extends NavContext {
  t: (key: PlainKey) => string;
}

/**
 * Every screen the palette can open on its own: the ones a path of no parameters names, that the
 * principal may see. A screen of a resource (an application's tabs) is offered by the page of that
 * resource, which knows which one it is. What a role cannot see is not here, so it is not found.
 */
export function paletteEntries(ctx: PaletteContext): PaletteEntry[] {
  const { t } = ctx;
  return REGISTERED.filter((entry) => !entry.to.includes("$") && shown(entry, ctx)).map((entry) => {
    const place = [...crumbsOf(entry.id), entry].filter((step) => step.kind !== "hidden" || step === entry).map((step) => t(step.label));
    const old = (RENAMED[entry.id] ?? []).map((key) => t(key));
    return {
      id: entry.id,
      to: entry.to,
      text: entry.kind === "area" ? t(entry.label) : entry.command !== undefined ? t(entry.command) : place.join(" › "),
      path: place.join(" › "),
      keywords: [entry.keywords !== undefined ? t(entry.keywords) : "", ...old].join(" ").trim(),
      ...(entry.shortcut !== undefined ? { shortcut: entry.shortcut } : {}),
    };
  });
}

// What the components that predate the register read. They are derived from it, never written by
// hand again; the next steps of the navigation work (the sidebar, the settings list, the palette)
// read the derivations above and these go.

/** A destination of the sidebar, as the sidebar and the palette draw it. */
export interface NavItem {
  label: PlainKey;
  to: ConsolePath;
  icon: LucideIcon;
  /** The two-key shortcut that opens it, such as ["g", "a"], and what the shortcut list calls it. */
  shortcut?: { keys: readonly string[]; description: PlainKey };
  /** Other words an operator might type for it in the palette. */
  keywords?: PlainKey;
  /**
   * Offered only where there is something to organise: more than one team, project or
   * environment, or someone who may start (`teams.manage`). A server nobody organised in
   * teams shows everyone else the sidebar it always had.
   */
  when?: "organised";
}

function navItem(entry: Dest): NavItem {
  if (entry.icon === undefined) throw new Error(`the area "${entry.id}" has no icon`);
  return {
    label: entry.label,
    to: entry.to,
    icon: entry.icon,
    ...(entry.shortcut !== undefined && entry.goTo !== undefined ? { shortcut: { keys: entry.shortcut, description: entry.goTo } } : {}),
    ...(entry.keywords !== undefined ? { keywords: entry.keywords } : {}),
    ...(entry.when === "organised" ? { when: "organised" as const } : {}),
  };
}

/** A server's destinations in the sidebar, top to bottom: what it runs, then the machine itself. */
export const NAV_GROUPS: readonly (readonly NavItem[])[] = sidebarFor("server").map((group) => group.map(navItem));

/** The fleet, every server at once ("All servers"): in the sidebar of a central that manages servers, or of a hub. */
export const FLEET_ITEM: NavItem = navItem(dest("fleet"));

export const SETTINGS_ITEM: NavItem = navItem(dest("settings"));
