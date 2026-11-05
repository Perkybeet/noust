import { hidden, tab } from "../../app/navCore";
import type { Dest, More, Strip } from "../../app/navCore";
import type { PlainKey } from "../../i18n";

// The fleet is not a bar of its own (4.1): the bar is the same twelve areas with or without one, and an
// area that has a fleet view says where it is (`fleetTo`). These are those views, each the fleet's face
// of an area (`faceOf`): the bar lights the area, the breadcrumbs say the area, and the palette finds
// the view by its phrase ("Certificates on every server"). The two keys an area answers to are the
// area's own. Updates has no area of its own: it is the second view of Servers, as Placements is of
// Applications, and the bar cannot hold it, so it is a tab of the page and the palette still finds it.

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
const network = face("fleet.network", "/fleet/network", "nav.fleetBar.network.label", "network", {
  faceOf: "network",
  keywords: "fleet.tabs.keywords.network",
  command: "fleet.tabs.command.network",
});
const security = face("fleet.security", "/fleet/security", "nav.fleetBar.security.label", "security", {
  faceOf: "security",
  keywords: "fleet.tabs.keywords.security",
  command: "fleet.tabs.command.security",
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
const updates = tab(servers, "updates", "nav.fleetBar.updates.label", {
  to: "/fleet/updates",
  scope: "fleet",
  exact: true,
  shortcut: ["g", "u"],
  goTo: "nav.fleetBar.updates.goTo",
  keywords: "fleet.tabs.keywords.updates",
  command: "fleet.tabs.command.updates",
});
const software = tab(servers, "software", "software.fleet.tab", {
  to: "/fleet/software",
  scope: "fleet",
  exact: true,
  keywords: "software.fleet.keywords",
  command: "software.fleet.command",
});
const activity = face("fleet.activity", "/fleet/activity", "nav.fleetBar.activity.label", "activity", {
  faceOf: "activity",
  keywords: "fleet.tabs.keywords.activity",
  command: "fleet.tabs.command.activity",
});

const alerts = face("fleet.alerts", "/fleet/alerts", "nav.fleetBar.alerts.label", "alerts", {
  faceOf: "alerts",
  keywords: "fleet.tabs.keywords.alerts",
  command: "fleet.tabs.command.alerts",
});

/**
 * The fleet: every server at once, as the faces of the areas that have one. The applications'
 * second view, the ones that run on several servers, is a tab of the fleet's Applications. The
 * register is app/nav.ts; this is one group of it.
 */
export const FLEET_SCREENS: Strip = [overview, servers, network, security, apps, placements, certificates, backups, updates, software, activity, alerts];

/** The two views of the fleet's applications, every server's and those that run on several. */
export const FLEET_APPS_SCREENS: Strip = [apps, placements];

/** The views of the fleet's servers: the list, what each one has waiting to be updated, and what each one has installed. */
export const FLEET_SERVERS_SCREENS: Strip = [servers, updates, software];
