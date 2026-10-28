import { hidden, tab } from "../../app/navCore";
import type { Dest, More, Strip } from "../../app/navCore";
import type { PlainKey } from "../../i18n";

// The fleet is not a bar of its own (4.1): the bar is the same twelve areas with or without one, and an
// area that has a fleet view says where it is (`fleetTo`). These are those views, each the fleet's face
// of an area (`faceOf`): the bar lights the area, the breadcrumbs say the area, and the palette finds
// the view by its phrase ("Certificates on every server"). The two keys an area answers to are the
// area's own, so only the screen that has no area of its own (Updates) has letters here.

const face = (id: string, to: Dest["to"], label: PlainKey, area: string, more: More = {}): Dest =>
  hidden(id, to, label, area, { scope: "fleet", parent: area, ...more });

const overview = face("fleet", "/fleet", "nav.fleetBar.overview.label", "overview", {
  faceOf: "overview",
  keywords: "servers.nav.fleet.keywords",
  command: "fleet.tabs.command.summary",
});
const servers = face("fleet.servers", "/fleet/servers", "nav.fleetBar.servers.label", "server", {
  faceOf: "server",
  keywords: "fleet.tabs.keywords.servers",
  command: "fleet.tabs.command.servers",
});
const apps = face("fleet.apps", "/fleet/apps", "nav.fleetBar.apps.label", "apps", {
  faceOf: "apps",
  keywords: "fleet.tabs.keywords.apps",
  command: "fleet.tabs.command.apps",
});
const placements = tab(apps, "placements", "placements.tab", { to: "/fleet/placements", scope: "fleet", exact: true, keywords: "placements.tabKeywords", command: "placements.tabCommand" });
const certificates = face("fleet.certificates", "/fleet/certificates", "nav.fleetBar.certificates.label", "domains", {
  faceOf: "domains",
  keywords: "fleet.tabs.keywords.certificates",
  command: "fleet.tabs.command.certificates",
});
const backups = face("fleet.backups", "/fleet/backups", "nav.fleetBar.backups.label", "backups", {
  faceOf: "backups",
  keywords: "fleet.tabs.keywords.backups",
  command: "fleet.tabs.command.backups",
});
const updates = face("fleet.updates", "/fleet/updates", "nav.fleetBar.updates.label", "server", {
  shortcut: ["g", "u"],
  goTo: "nav.fleetBar.updates.goTo",
  keywords: "fleet.tabs.keywords.updates",
  command: "fleet.tabs.command.updates",
});
const activity = face("fleet.activity", "/fleet/activity", "nav.fleetBar.activity.label", "activity", {
  faceOf: "activity",
  keywords: "fleet.tabs.keywords.activity",
  command: "fleet.tabs.command.activity",
});

/**
 * The fleet: every server at once, as the faces of the areas that have one. The applications'
 * second view, the ones that run on several servers, is a tab of the fleet's Applications. The
 * register is app/nav.ts; this is one group of it.
 */
export const FLEET_SCREENS: Strip = [overview, servers, apps, placements, certificates, backups, updates, activity];

/** The two views of the fleet's applications, every server's and those that run on several: the one strip the fleet's pages draw. */
export const FLEET_APPS_SCREENS: Strip = [apps, placements];
