/**
 * The panels of Home, as pure functions so every rule is tested without a page: the two that come with the
 * console and what is in each, how a panel a person saved becomes one the console can draw (a widget their role
 * may not see is left out), and the id a new panel is kept under.
 *
 * "This server" follows the server chosen in the selector and "Fleet" is every server; a person's own panels
 * choose one of the two. The panels of a server and of the fleet are the same page over a different set of servers.
 */

import { isOffered, sizesOf, widgetOf } from "../../components/widgets/catalog";
import { fromDocument } from "../../components/widgets/model";
import type { LayoutDocument, Placed, SizeKey } from "../../components/widgets/model";
import type { PanelScope } from "../../components/widgets/types";
import type { T } from "../../i18n";

/** The panel that comes with the console for each scope, and the id it is kept under if its person changes it. */
export const BUILTIN_IDS: Readonly<Record<PanelScope, string>> = { server: "server", fleet: "fleet" };

/** What a panel is called by the route that lists the panels (`POST /api/auth/dashboards/order`) and by the two that ship: never given to a person's own. */
const RESERVED: ReadonlySet<string> = new Set(["order", "new", ...Object.values(BUILTIN_IDS)]);

/** One panel a person saved: kept by the server for an account, in the browser for the access token. */
export interface SavedPanel {
  id: string;
  name: string;
  scope: PanelScope;
  layout: LayoutDocument;
  /** What was last written here has not reached the server yet, so a read of the server does not replace it. */
  dirty: boolean;
}

/** A panel the console can draw. */
export interface Panel {
  id: string;
  name: string;
  scope: PanelScope;
  layout: Placed[];
  /** One of the two that come with the console: it cannot be deleted, only put back as it came. */
  builtin: boolean;
  /** A person changed what came with the console. */
  customised: boolean;
}

/** What a panel starts with: the widget, and the size it is drawn at. */
type Start = readonly (readonly [string, SizeKey])[];

/**
 * "This server": what an operator wants to see first on the server they are on (the panel the design calls
 * the default of a server): what needs them, the six figures, then the machine, the work, the certificates and the
 * backups, the applications with the shortcuts, and the last deployments beside what happened.
 */
const SERVER_START: Start = [
  ["attention", "wide"],
  ["figures", "wide"],
  ["resources", "small"],
  ["work", "small"],
  ["certificates", "small"],
  ["backups", "small"],
  ["apps", "medium"],
  ["actions", "medium"],
  ["deployments", "large"],
  ["activity", "large"],
];

/** "Fleet": the servers first, then what needs the operator anywhere, then what is going on. */
const FLEET_START: Start = [
  ["attention", "wide"],
  ["fleet", "xlarge"],
  ["apps", "medium"],
  ["updates", "medium"],
  ["work", "small"],
  ["certificates", "small"],
  ["activity", "large"],
  ["backups", "small"],
  ["alerts", "small"],
];

const STARTS: Readonly<Record<PanelScope, Start>> = { server: SERVER_START, fleet: FLEET_START };

/** The layout a panel of this scope comes with, without the widgets a credential that does not hold what they read may not have. */
export function defaultLayout(scope: PanelScope, can: (permission: string) => boolean): Placed[] {
  return STARTS[scope].flatMap(([type, size]) => {
    const widget = widgetOf(type);
    return widget !== undefined && isOffered(widget, scope, can) ? [{ id: type, type, size, settings: { ...widget.settings } }] : [];
  });
}

/**
 * The panels a person has: the two that come with the console, with what the person saved over them where they did,
 * then the ones they made, in the order they were made. A saved layout that is damaged, or that is of no widget this
 * console draws for this role, does not replace what comes with the console.
 */
export function panelsOf(saved: readonly SavedPanel[], can: (permission: string) => boolean, t: T, offerFleet: boolean): Panel[] {
  const builtin = (scope: PanelScope): Panel => {
    const id = BUILTIN_IDS[scope];
    const mine = saved.find((panel) => panel.id === id && panel.scope === scope);
    const read = mine === undefined ? null : fromDocument(mine.layout, sizesOf(scope, can));
    return {
      id,
      name: t(scope === "server" ? "overview.panels.server" : "overview.panels.fleet"),
      scope,
      layout: read ?? defaultLayout(scope, can),
      builtin: true,
      customised: read !== null,
    };
  };
  const own = saved
    .filter((panel) => !Object.values(BUILTIN_IDS).includes(panel.id))
    .map((panel): Panel => ({ id: panel.id, name: panel.name, scope: panel.scope, layout: fromDocument(panel.layout, sizesOf(panel.scope, can)) ?? [], builtin: false, customised: false }));
  return [builtin("server"), ...(offerFleet ? [builtin("fleet")] : []), ...own.filter((panel) => offerFleet || panel.scope === "server")];
}

/** The id a panel with this name is kept under: its name in lower case letters, digits and hyphens, which no other panel has. */
export function slugOf(name: string, taken: Iterable<string>): string {
  const base =
    name
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 36)
      .replace(/-+$/g, "") || "panel";
  const used = new Set<string>([...taken, ...RESERVED]);
  if (!used.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base}-${String(n)}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** What a new panel is made of: a copy of the one that comes with the console for its scope, or nothing. */
export function startingLayout(scope: PanelScope, start: "copy" | "empty", can: (permission: string) => boolean): Placed[] {
  return start === "copy" ? defaultLayout(scope, can) : [];
}
