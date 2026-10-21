import { area, tab, tabItem } from "../../app/navCore";
import type { Dest, Strip, TabItem } from "../../app/navCore";
import type { PlainKey } from "../../i18n";

// The fleet is a scope, not one destination of a server's bar (2.3 of the navigation design): its
// own seven destinations are the sidebar while "All servers" (or "This central") is selected, and
// the strip of eight tabs the fleet pages drew is gone from the register. Each has the letter it
// has on a server's bar for the same noun (`g a` is Applications in both), because only one of the
// two bars is ever on screen.

const view = (id: string, to: Dest["to"], label: PlainKey, shortcut: readonly [string, string], goTo: PlainKey, more: Partial<Dest>): Dest =>
  area(id, to, label, { scope: "fleet", group: "fleet", shortcut, goTo, ...more });

const overview = view("fleet", "/fleet", "nav.fleetBar.overview.label", ["g", "o"], "nav.fleetBar.overview.goTo", {
  keywords: "servers.nav.fleet.keywords",
  command: "fleet.tabs.command.summary",
});
const servers = view("fleet.servers", "/fleet/servers", "nav.fleetBar.servers.label", ["g", "v"], "nav.fleetBar.servers.goTo", {
  keywords: "fleet.tabs.keywords.servers",
  command: "fleet.tabs.command.servers",
});
const apps = view("fleet.apps", "/fleet/apps", "nav.fleetBar.apps.label", ["g", "a"], "nav.fleetBar.apps.goTo", {
  keywords: "fleet.tabs.keywords.apps",
  command: "fleet.tabs.command.apps",
});
const placements = tab(apps, "placements", "placements.tab", { to: "/fleet/placements", scope: "fleet", exact: true, keywords: "placements.tabKeywords", command: "placements.tabCommand" });
const certificates = view("fleet.certificates", "/fleet/certificates", "nav.fleetBar.certificates.label", ["g", "n"], "nav.fleetBar.certificates.goTo", {
  keywords: "fleet.tabs.keywords.certificates",
  command: "fleet.tabs.command.certificates",
});
const backups = view("fleet.backups", "/fleet/backups", "nav.fleetBar.backups.label", ["g", "k"], "nav.fleetBar.backups.goTo", {
  keywords: "fleet.tabs.keywords.backups",
  command: "fleet.tabs.command.backups",
});
const updates = view("fleet.updates", "/fleet/updates", "nav.fleetBar.updates.label", ["g", "u"], "nav.fleetBar.updates.goTo", {
  keywords: "fleet.tabs.keywords.updates",
  command: "fleet.tabs.command.updates",
});
const activity = view("fleet.activity", "/fleet/activity", "nav.fleetBar.activity.label", ["g", "y"], "nav.fleetBar.activity.goTo", {
  keywords: "fleet.tabs.keywords.activity",
  command: "fleet.tabs.command.activity",
});

/**
 * The fleet: every server at once. Its destinations are the sidebar of the fleet scope; the
 * applications' second view, the ones that run on several servers, is a tab of Applications.
 * The register is app/nav.ts; this is one group of it.
 */
export const FLEET_SCREENS: Strip = [overview, servers, apps, placements, certificates, backups, updates, activity];

export interface FleetTabItem extends TabItem {
  /** Its name in the command palette, where it stands alone: "Certificates on every server". */
  command: PlainKey;
}

const STRIP_LABEL: Readonly<Record<string, PlainKey>> = {
  fleet: "fleet.tabs.summary",
  "fleet.servers": "fleet.tabs.servers",
  "fleet.apps": "fleet.tabs.apps",
  "fleet.certificates": "fleet.tabs.certificates",
  "fleet.backups": "fleet.tabs.backups",
  "fleet.updates": "fleet.tabs.updates",
  "fleet.activity": "fleet.tabs.activity",
};

/**
 * The strip the fleet's pages still draw above their content, for as long as they draw one: the
 * sidebar of the fleet scope has taken its place, and the page that draws it (features/fleet) is
 * the one to stop.
 */
export const FLEET_TABS: readonly FleetTabItem[] = FLEET_SCREENS.map((entry, index): FleetTabItem => {
  if (entry.command === undefined) throw new Error(`the fleet view "${entry.id}" has no palette phrase`);
  // The strip keeps the names it always had ("Noust and system updates"): the sidebar's are shorter, for a column of one word each.
  return { ...tabItem(entry, index === 0), label: STRIP_LABEL[entry.id] ?? tabItem(entry, index === 0).label, command: entry.command };
});
