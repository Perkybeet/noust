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
import { ALERTS_SCREENS } from "../features/nav/alerts";
import { APP_SCREENS } from "../features/nav/app";
import { APPS_SCREENS } from "../features/nav/apps";
import { BACKUPS_SCREENS } from "../features/nav/backups";
import { CATALOG_SCREENS } from "../features/nav/catalog";
import { DATABASES_SCREENS, DATABASE_SCREENS } from "../features/nav/databases";
import { DOMAINS_SCREENS } from "../features/nav/domains";
import { FLEET_SCREENS } from "../features/nav/fleet";
import { iconOf, sectionIcon } from "../features/nav/icons";
import { ACTIVITY_SCREENS, CRON_SCREENS, LOGS_SCREENS } from "../features/nav/machine";
import { NETWORK_SCREENS } from "../features/nav/network";
import { OVERVIEW_SCREENS } from "../features/nav/overview";
import { PROJECTS_SCREENS } from "../features/nav/projects";
import { SECURITY_SCREENS } from "../features/nav/security";
import { SERVER_SCREENS, SERVICE_SCREENS } from "../features/nav/server";
import { RENAMED, SETTINGS_SCREENS, groupSettings } from "../features/nav/settings";
import { TEAM_SCREENS } from "../features/nav/team";
import { VIEWS } from "../features/nav/views";
import type { AreaView } from "../features/nav/views";
import type { PlainKey } from "../i18n";
import { fleetPathOf, isCentralOnlyPath, serverPathOfFleet } from "./nodeRoute";
import { REDIRECT_ROWS } from "./redirects";
import { shown, tabsOf } from "./navCore";
import type { ConsolePath, Dest, NavContext, Scope, TabItem } from "./navCore";

export { INTERIM, areaLabelKey, isStaged, tabsOf, withCounts } from "./navCore";
export type { ConsolePath, Dest, Kind, NavContext, Owner, Scope, Strip, TabItem, When } from "./navCore";
export { APP_TABS } from "../features/nav/app";
export { FLEET_APPS_SCREENS, FLEET_SERVERS_SCREENS } from "../features/nav/fleet";
export { TEAM_TABS } from "../features/nav/team";

/** The register itself, filled in the order the lists draw the groups. */
const REGISTERED: Dest[] = [];
const BY_ID = new Map<string, Dest>();

/** Every screen, in the order its list draws it. */
export const DESTS: readonly Dest[] = REGISTERED;

// The areas of the bar in the order it draws them (home; applications and data; network; observe;
// protect; infrastructure), then what is under them, then the fleet's faces, Settings and the account.
for (const group of [
  OVERVIEW_SCREENS,
  APPS_SCREENS,
  PROJECTS_SCREENS,
  CATALOG_SCREENS,
  DATABASES_SCREENS,
  CRON_SCREENS,
  DOMAINS_SCREENS,
  NETWORK_SCREENS,
  LOGS_SCREENS,
  ALERTS_SCREENS,
  ACTIVITY_SCREENS,
  BACKUPS_SCREENS,
  SECURITY_SCREENS,
  SERVER_SCREENS,
  SERVICE_SCREENS,
  DATABASE_SCREENS,
  APP_SCREENS,
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
  "/databases/engines/$engine/settings": "one engine's settings, opened from its menu in Engines",
  "/domains/sites/$site": "one site's web server file, opened from the Sites tab",
  "/domains/dns/zones/$zone": "one zone of Noust DNS, opened from its list of zones",
  "/activity/tasks/$id": "one task, opened from the task centre in the top bar or from the list of tasks",
  "/fleet/jobs/$id": "one fleet job, opened from the notice that started it",
  "/projects/$team/$project": "one project, opened from the Projects list",
  "/server/services/$name/unit": "a service's unit file, opened from the service's page",
  "/server/docker/containers/$ref": "one container from the inside, opened from the list of containers",
  "/server/docker/stacks/$name": "one stack, opened from the list of stacks",
  "/server/docker/compose/$name": "the Compose document of one stack, opened from the stack's page",
  "/fleet/$": "any section of the console seen from All servers that has no page of the fleet of its own yet: the table of servers stands in for it (item 196)",
};

/**
 * Where each exception sits in the tree, for the breadcrumbs: the entry it is opened from, and what names
 * the page (a parameter of its route, the resource's name, or a text).
 */
export const EXCEPTION_CRUMBS: Readonly<Record<string, { from: string; name: { param: string; prefix?: string } | { key: PlainKey } }>> = {
  "/apps/$domain/deployments/$id": { from: "app.deployments", name: { param: "id", prefix: "#" } },
  "/catalog/$name": { from: "catalog", name: { param: "name" } },
  "/databases/engines/$engine/settings": { from: "databases.engines", name: { param: "engine" } },
  "/domains/sites/$site": { from: "domains.sites", name: { param: "site" } },
  "/domains/dns/zones/$zone": { from: "domains.dns.noust", name: { param: "zone" } },
  "/activity/tasks/$id": { from: "activity.tasks", name: { param: "id", prefix: "#" } },
  "/fleet/jobs/$id": { from: "activity", name: { param: "id", prefix: "#" } },
  "/projects/$team/$project": { from: "apps.projects", name: { param: "project" } },
  "/server/services/$name/unit": { from: "service", name: { key: "services.actions.unitFile" } },
  "/server/docker/containers/$ref": { from: "server.docker.containers", name: { param: "ref" } },
  "/server/docker/stacks/$name": { from: "server.docker.stacks", name: { param: "name" } },
  "/server/docker/compose/$name": { from: "server.docker.stacks", name: { key: "docker.compose.crumb" } },
  "/integrations/github/callback": { from: "settings.integrations", name: { key: "settings.integrations.callback.title" } },
  "/integrations/gitlab/callback": { from: "settings.integrations", name: { key: "gitlab.callback.title" } },
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
 * The URLs that change in 4.0, each redirecting to its new place (docs/UPGRADING-4.0.md carries
 * this table as "where it is now"). The rows are data in app/redirects.ts, typed here: a path
 * that is not a route fails the build. `routes/redirects.test.tsx` walks it through the real router.
 */
export const REDIRECTS: readonly Redirect[] = REDIRECT_ROWS;

/**
 * The main bar: the areas, grouped and in order, the same whether the console holds a fleet or not (Settings is pinned
 * under them and is not here). An area is offered when something in it is: its own page, one of its tabs, one of its
 * views (`areaOffered`); what the server cannot do is not given as an option (item 187). Where an area opens from the
 * fleet's pages is `areaTarget`'s, and which of its sections it opens at on a server that lacks the first, `areaLanding`'s.
 */
export function sidebarFor(ctx: NavContext = {}): Dest[][] {
  const groups = new Map<string, Dest[]>();
  for (const entry of REGISTERED) {
    if (entry.kind !== "area" || entry.group === "pinned" || !areaOffered(entry, ctx)) continue;
    const key = entry.group ?? entry.id;
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  return [...groups.values()];
}

/** The sections a list draws for an area on a server's pages: its own page, then its tabs, in the register's order. */
function sectionsOfArea(area: Dest): Dest[] {
  return REGISTERED.filter((entry) => (entry === area || (entry.kind === "tab" && rootOf(entry) === area)) && entry.scope !== "fleet" && !entry.to.includes("$"));
}

/** Whether a view of an area's page is offered: its own function, when it needs one. */
function viewOffered(view: AreaView, ctx: NavContext): boolean {
  return view.feature === undefined || ctx.features === undefined || ctx.features(view.feature);
}

/** The views of an area that are offered; none when the area has none declared (its page is one thing). */
export function offeredViews(area: Dest, ctx: NavContext): readonly AreaView[] {
  return (VIEWS[area.id] ?? []).filter((view) => viewOffered(view, ctx));
}

/** Whether the area's own page (its first section) is offered: it asks for its function, and it has a view to show. */
export function rootOffered(area: Dest, ctx: NavContext): boolean {
  const views = VIEWS[area.id];
  return shown(area, ctx) && (views === undefined || offeredViews(area, ctx).length > 0);
}

/**
 * Whether an area has anything to offer on a server: its own page, or one of its tabs. An area none of whose sections the
 * server can do is absent from the bar, as a section it cannot do is absent from the column.
 */
export function areaOffered(area: Dest, ctx: NavContext): boolean {
  return rootOffered(area, ctx) || sectionsOfArea(area).some((entry) => entry !== area && shown(entry, ctx));
}

/** Where an area opens: an address and, for an area whose page is made of views, the search that names the first one offered. */
export interface Landing {
  to: ConsolePath;
  search?: Readonly<Record<string, string>>;
}

/**
 * Where an area opens on a server: its own page when the server can do it and, when it cannot (the first section of Network
 * is the published ports, which a hub does not have), the first of its sections it can. A view is the page with a search, so an
 * area whose first view is not offered opens at the first that is (Logs on a machine with no applications opens at the system's).
 */
export function areaLanding(area: Dest, ctx: NavContext = {}): Landing {
  if (rootOffered(area, ctx)) {
    const first = offeredViews(area, ctx)[0];
    return first?.search !== undefined && VIEWS[area.id]?.[0] !== first ? { to: area.to, search: first.search } : { to: area.to };
  }
  const other = sectionsOfArea(area).find((entry) => entry !== area && shown(entry, ctx));
  return { to: (other ?? area).to };
}

/**
 * Where an area opens: its page on a server, and from "All servers" its address under /fleet (`fleetTarget`), where its
 * section has a face of its own or the table of servers every other section shows meanwhile. A section never has no place
 * to open: the fleet draws what a server draws, section for section.
 */
export function areaTarget(entry: Dest, scope: Scope): ConsolePath {
  return scope === "fleet" ? fleetTarget(entry) : entry.to;
}

/**
 * The address "All servers" gives a section: its face where the register names one (`fleetTo`), and otherwise the same
 * address under /fleet (`/databases` is `/fleet/databases`, `nodeRoute.fleetPathOf`), which draws what the section says
 * of every server. The scope is in the address, so every link built from here stays in it.
 */
export function fleetTarget(entry: Dest): ConsolePath {
  if (entry.fleetTo !== undefined) return entry.fleetTo;
  // The fleet's own pages are already under /fleet, and the central's (Settings, the account) are no server's: neither has a scope to move to.
  if (entry.scope === "fleet" || entry.to.includes("$") || isCentralOnlyPath(entry.to)) return entry.to;
  return fleetPathOf(entry.to) as ConsolePath;
}

/** The same tabs for an id the caller has as text: tests and the palette, which draw nothing on a route. */
export function tabsById(id: string, ctx: NavContext = {}): TabItem[] {
  return tabsOf([dest(id), ...REGISTERED.filter((entry) => entry.parent === id)], ctx);
}

export { RENAMED, SETTINGS_GROUPS, groupSettings, settingsGroupOf } from "../features/nav/settings";

/** The sections of Settings, in titled groups, from the whole register. */
export function settingsGroups(ctx: NavContext = {}): { id: string; sections: Dest[] }[] {
  return groupSettings(
    REGISTERED.filter((entry) => entry.id === "settings" || entry.parent === "settings"),
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
export function rootOf(entry: Dest): Dest {
  let current = entry;
  for (let guard = 0; current.parent !== undefined && guard < 8; guard += 1) current = dest(current.parent);
  return current;
}

/**
 * Screens that live at an address of their own and belong to an area all the same: what an outside
 * service sends the browser back to, and a job of the fleet. They are in `EXCEPTIONS`, so no entry
 * of the register says which area they are under.
 */
const OWNED_BY: Readonly<Record<string, string>> = { "/fleet/jobs": "activity", "/integrations": "settings" };

/**
 * The area, or the resource, a path is under: the entry whose address is the longest start of it,
 * and where its chain of parents ends. One answer for the bar (which item is current), the second
 * column (which resource's sections), the breadcrumbs and the title. A page that is under nothing is
 * `undefined`. The home page owns `/` and nothing beneath it: every address starts with it.
 */
export function areaOf(pathname: string): Dest | undefined {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  let best: { entry: Dest; length: number } | undefined;
  const consider = (entry: Dest, prefix: string): void => {
    // The home of a scope owns its own address and nothing beneath it: every other address starts with it.
    const under = prefix === "/" || prefix === "/fleet" ? path === prefix : isUnder(path, prefix);
    if (under && (best === undefined || prefix.length > best.length)) best = { entry, length: prefix.length };
  };
  for (const entry of REGISTERED) consider(entry, staticPrefix(entry.to));
  for (const [prefix, id] of Object.entries(OWNED_BY)) consider(dest(id), prefix);
  if (best !== undefined) return rootOf(best.entry);
  // A section of All servers that has no face of its own is the server's address under /fleet: it is under the same area.
  const server = serverPathOfFleet(path);
  return server === null || server === path ? undefined : areaOf(server);
}

/**
 * Where "All servers" opens from a page of a server: the same section's page under /fleet (its face, or the table of servers that
 * stands in for it), and for a page of one thing (an application) or one that is under no section, its area's. The summary of the
 * fleet when the page is under no area.
 */
export function allServersTargetOf(pathname: string): ConsolePath {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  const entry = entryAt(path);
  if (entry?.to === path) return fleetTarget(entry);
  const area = areaOf(path);
  return area === undefined ? "/fleet" : fleetTarget(area);
}

/**
 * Where a server opens from a page of All servers: the section the page is the fleet's page of, on that server (`/fleet/databases` is
 * `/databases`; the fleet's certificates are the server's Certificates), and for a page that is only the fleet's (the applications that
 * run on several servers) its area's page. The server's home when the page is under no area.
 */
export function serverTargetOf(pathname: string): ConsolePath {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  const section = REGISTERED.find((entry) => entry.scope !== "fleet" && !entry.to.includes("$") && fleetTarget(entry) === path);
  if (section !== undefined) return section.to;
  const plain = serverPathOfFleet(path);
  if (plain !== null && entryAt(plain)?.to === plain) return plain;
  return areaOf(path)?.to ?? "/";
}

/**
 * Whether an address under /fleet is a page of the fleet of its own (the applications of every server, the servers, the
 * certificates...), as opposed to a section the table of servers stands in for until its area builds one (`/fleet/$`).
 */
export function isFleetFace(pathname: string): boolean {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return REGISTERED.some((entry) => entry.scope === "fleet" && entry.to === path) || isUnder(path, "/fleet/jobs");
}

/**
 * The register's entry for an address on a server: the one whose path is the longest start of it. What a server must be
 * able to do for the page is the entry's `feature`, and that is what `FeatureGate` asks. Undefined for a page that is under
 * nothing.
 */
export function entryAt(pathname: string): Dest | undefined {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  let best: { entry: Dest; length: number } | undefined;
  for (const entry of REGISTERED) {
    if (entry.scope === "fleet") continue;
    const prefix = staticPrefix(entry.to);
    const under = prefix === "/" ? path === "/" : isUnder(path, prefix);
    if (under && (best === undefined || prefix.length > best.length)) best = { entry, length: prefix.length };
  }
  return best?.entry;
}

/**
 * The `g` shortcuts, in the order the shortcut list shows them, of one scope (or of all). The areas
 * are in both: the same two keys open each from a server and from the fleet.
 */
export function shortcuts(scope?: Scope, ctx: NavContext = {}): { id: string; keys: readonly string[]; description: PlainKey; to: ConsolePath }[] {
  return REGISTERED.flatMap((entry) => {
    if (entry.shortcut === undefined || entry.goTo === undefined) return [];
    if (scope !== undefined && entry.scope !== "both" && entry.scope !== scope) return [];
    // What the server cannot do has no key: a shortcut is an option like any other. All servers has every section.
    if (scope !== "fleet" && (entry.kind === "area" ? !areaOffered(entry, ctx) : !shown(entry, ctx))) return [];
    // The fleet's pages open an area at its fleet face, where it has one.
    const to = scope === undefined ? entry.to : scope === "fleet" ? areaTarget(entry, scope) : entry.kind === "area" ? areaLanding(entry, ctx).to : entry.to;
    return [{ id: entry.id, keys: entry.shortcut, description: entry.goTo, to }];
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
  /** The names it had before, each of them whole (the palette ranks one typed whole just under the current name). */
  aliases: readonly string[];
  shortcut?: readonly string[];
  /** What it is, drawn: the same in every list (features/nav/icons.ts). */
  icon: LucideIcon;
}

export interface PaletteContext extends NavContext {
  t: (key: PlainKey) => string;
  /** The scope the person is in: from All servers every section opens at its page under /fleet, so choosing one never moves the selector. */
  scope?: Scope;
}

/**
 * Every screen the palette can open on its own: the ones a path of no parameters names, that the
 * principal may see. A screen of a resource (an application's tabs) is offered by the page of that
 * resource, which knows which one it is. What a role cannot see is not here, so it is not found.
 */
export function paletteEntries(ctx: PaletteContext): PaletteEntry[] {
  const { t } = ctx;
  // What the fleet already has a page for under its own name ("Certificates on every server") is that page; the section it stands for is not listed twice.
  const faces = new Set(REGISTERED.filter((entry) => entry.scope === "fleet").map((entry) => entry.to as string));
  const fleet = ctx.scope === "fleet";
  const inFleet = (entry: Dest): boolean => entry.scope !== "fleet" && (entry.kind !== "hidden" || entry.column !== undefined) && !isCentralOnlyPath(entry.to);
  return REGISTERED.filter((entry) => !entry.to.includes("$") && shown(entry, ctx) && !(fleet && inFleet(entry) && faces.has(fleetTarget(entry)))).map((entry) => {
    const place = [...crumbsOf(entry.id), entry].filter((step) => step.kind !== "hidden" || step === entry).map((step) => t(step.label));
    const old = (RENAMED[entry.id] ?? []).map((key) => t(key));
    // The fleet's faces are named by their phrase ("Certificates on every server"): their bare name is the area's.
    const bare = entry.kind === "area";
    return {
      id: entry.id,
      to: fleet && inFleet(entry) ? fleetTarget(entry) : entry.to,
      scope: entry.scope,
      text: bare ? t(entry.label) : entry.command !== undefined ? t(entry.command) : place.join(" › "),
      path: place.join(" › "),
      keywords: [entry.keywords !== undefined ? t(entry.keywords) : "", ...old].join(" ").trim(),
      aliases: old,
      ...(entry.shortcut !== undefined ? { shortcut: entry.shortcut } : {}),
      // A tab has no icon of its own: the area's it is under.
      icon: sectionIcon(entry) ?? areaIcon(entry),
    };
  });
}
