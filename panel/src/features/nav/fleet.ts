import { Network } from "lucide-react";

import { area, stripEntries, tab, tabItem } from "../../app/navCore";
import type { Dest, Strip, TabItem } from "../../app/navCore";
import type { PlainKey } from "../../i18n";

const fleet = area("fleet", "/fleet", "servers.nav.fleet.label", Network, {
  scope: "fleet",
  group: "fleet",
  shortcut: ["g", "f"],
  goTo: "servers.nav.fleet.goTo",
  keywords: "servers.nav.fleet.keywords",
  rootLabel: "fleet.tabs.summary",
  command: "fleet.tabs.command.summary",
});

const view = (name: string, label: PlainKey, keywords: PlainKey, command: PlainKey): Dest => tab(fleet, name, label, { scope: "fleet", keywords, command });

/**
 * The fleet: every server at once. In 3.x its views are a strip of tabs, and the sidebar offers
 * only the fleet; each view has a phrase for the palette, where it stands alone. The register is
 * app/nav.ts; this is one group of it.
 */
export const FLEET_SCREENS: Strip = [
  fleet,
  view("servers", "fleet.tabs.servers", "fleet.tabs.keywords.servers", "fleet.tabs.command.servers"),
  view("apps", "fleet.tabs.apps", "fleet.tabs.keywords.apps", "fleet.tabs.command.apps"),
  view("placements", "placements.tab", "placements.tabKeywords", "placements.tabCommand"),
  view("certificates", "fleet.tabs.certificates", "fleet.tabs.keywords.certificates", "fleet.tabs.command.certificates"),
  view("backups", "fleet.tabs.backups", "fleet.tabs.keywords.backups", "fleet.tabs.command.backups"),
  view("updates", "fleet.tabs.updates", "fleet.tabs.keywords.updates", "fleet.tabs.command.updates"),
  view("activity", "fleet.tabs.activity", "fleet.tabs.keywords.activity", "fleet.tabs.command.activity"),
];

export interface FleetTabItem extends TabItem {
  /** Its name in the command palette, where it stands alone: "Certificates on every server". */
  command: PlainKey;
}

export const FLEET_TABS: readonly FleetTabItem[] = stripEntries(FLEET_SCREENS, {}).map((entry, index): FleetTabItem => {
  if (entry.command === undefined) throw new Error(`the fleet view "${entry.id}" has no palette phrase`);
  return { ...tabItem(entry, index === 0), command: entry.command };
});
