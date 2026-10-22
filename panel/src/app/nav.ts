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
import { DATABASES_SCREENS, DATABASE_SCREENS } from "../features/nav/databases";
import { DOMAINS_SCREENS } from "../features/nav/domains";
import { FLEET_SCREENS } from "../features/nav/fleet";
import { iconOf, sectionIcon } from "../features/nav/icons";
import { ACTIVITY_SCREENS, CRON_SCREENS, LOGS_SCREENS } from "../features/nav/machine";
import { OVERVIEW_SCREENS } from "../features/nav/overview";
import { PROJECTS_SCREENS } from "../features/nav/projects";
import { SERVER_SCREENS, SERVICE_SCREENS } from "../features/nav/server";
import { RENAMED, SETTINGS_SCREENS, groupSettings } from "../features/nav/settings";
import type { SettingsBlock } from "../features/nav/settings";
import { TEAM_SCREENS } from "../features/nav/team";
import type { PlainKey } from "../i18n";
import { REDIRECT_ROWS } from "./redirects";
import { shown, tabsOf } from "./navCore";
import type { ConsolePath, Dest, NavContext, Scope, TabItem } from "./navCore";

export { INTERIM, isStaged, tabsOf, withCounts } from "./navCore";
export type { ConsolePath, Dest, Kind, NavContext, Owner, Scope, Strip, TabItem, When } from "./navCore";
export { APP_TABS } from "../features/nav/app";
export { FLEET_APPS_SCREENS } from "../features/nav/fleet";
export { TEAM_TABS } from "../features/nav/team";

/** The register itself, filled in the order the lists draw the groups. */
const REGISTERED: Dest[] = [];
const BY_ID = new Map<string, Dest>();

/** Every screen, in the order its list draws it. */
export const DESTS: readonly Dest[] = REGISTERED;

// The server scope first, in its three groups separated by space (what runs, what happens, the
// machine), then what is under them, then the fleet, Settings and the account.
for (const group of [
  OVERVIEW_SCREENS,
  PROJECTS_SCREENS,
  APPS_SCREENS,
  DATABASES_SCREENS,
  DOMAINS_SCREENS,
  BACKUPS_SCREENS,
  CRON_SCREENS,
  LOGS_SCREENS,
  ACTIVITY_SCREENS,
  SERVER_SCREENS,
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
  /** The destination is a page of one thing with no filters: whatever search the old address carried is dropped. */
  plain?: true;
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

export { RENAMED, SETTINGS_GROUPS, groupSettings, settingsGroupOf } from "../features/nav/settings";
export type { SettingsBlock } from "../features/nav/settings";

/** The sections of Settings a block holds, in titled groups, from the whole register. */
export function settingsGroups(block: SettingsBlock, ctx: NavContext = {}): { id: string; sections: Dest[] }[] {
  return groupSettings(
    REGISTERED.filter((entry) => entry.id === "settings" || entry.parent === "settings"),
    block,
    ctx,
  );
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

/** Whether `path` is `prefix` or beneath it. */
export function isUnder(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(prefix === "/" ? "//" : `${prefix}/`);
}

/** The static start of a path with parameters: `/teams/$team/members` begins at `/teams`. */
export function staticPrefix(to: string): string {
  const at = to.indexOf("/$");
  return at <= 0 ? to : to.slice(0, at);
}

/** The area, or the resource, an entry belongs to: where its chain of parents ends. */
function rootOf(entry: Dest): Dest {
  let current = entry;
  for (let guard = 0; current.parent !== undefined && guard < 8; guard += 1) current = dest(current.parent);
  return current;
}

/**
 * The addresses that keep an area lit in the sidebar besides its own: the screens that are under
 * it in the tree but live at another start (the recipes are at `/catalog` and belong to
 * Applications, a team's pages are at `/teams` and belong to Projects, the Map is Overview's).
 */
export function litBy(area: Dest): string[] {
  const own = staticPrefix(area.to);
  const others = REGISTERED.filter((entry) => entry !== area && rootOf(entry) === area).map((entry) => staticPrefix(entry.to));
  return [...new Set(others.filter((prefix) => !isUnder(prefix, own)))];
}

/**
 * The `g` shortcuts, in the order the shortcut list shows them, of one scope (or of all). Settings
 * is in both: the same two keys open it from a server and from the fleet.
 */
export function shortcuts(scope?: Scope): { id: string; keys: readonly string[]; description: PlainKey; to: ConsolePath }[] {
  return REGISTERED.flatMap((entry) => {
    if (entry.shortcut === undefined || entry.goTo === undefined) return [];
    if (scope !== undefined && entry.scope !== "both" && entry.scope !== scope) return [];
    return [{ id: entry.id, keys: entry.shortcut, description: entry.goTo, to: entry.to }];
  });
}

/** The icon of the area a screen is under: every area has one, and a screen that is not under one is a typo in the register. */
function areaIcon(entry: Dest): LucideIcon {
  return iconOf(rootOf(entry));
}

export interface PaletteEntry {
  id: string;
  to: ConsolePath;
  scope: Scope | "both";
  /** What the palette lists it as: its label, its phrase, or its place in text. */
  text: string;
  /** Where it lives, in words: "Server › System updates". */
  path: string;
  /** Search words, the old names included. */
  keywords: string;
  shortcut?: readonly string[];
  /** What it is, drawn: the same in every list (features/nav/icons.ts). */
  icon: LucideIcon;
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
    // The fleet's destinations are named by their phrase ("Certificates on every server"): their bare name is also a server's.
    const bare = entry.kind === "area" && entry.scope !== "fleet";
    return {
      id: entry.id,
      to: entry.to,
      scope: entry.scope,
      text: bare ? t(entry.label) : entry.command !== undefined ? t(entry.command) : place.join(" › "),
      path: place.join(" › "),
      keywords: [entry.keywords !== undefined ? t(entry.keywords) : "", ...old].join(" ").trim(),
      ...(entry.shortcut !== undefined ? { shortcut: entry.shortcut } : {}),
      // A tab has no icon of its own: the area's it is under.
      icon: sectionIcon(entry) ?? areaIcon(entry),
    };
  });
}
