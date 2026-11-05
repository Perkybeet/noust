import type { LucideIcon } from "lucide-react";

import type { Dest } from "../../app/navCore";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import type { ObjectName } from "../../components/ui/objectIcons";

/**
 * Which object each area and each section of a list on the side is about. The icon is the object's,
 * from the kit's map (components/ui/objectIcons.ts), so a screen, a button and a menu of one thing
 * look like one thing (docs/DESIGN.md I-5, NV-19). A tab has none of its own, because no strip
 * draws one (its width is the budget): the palette shows the icon of its area.
 *
 * A table of its own, and not an argument of each entry, so that a page that draws a strip of tabs
 * reads the group of screens (features/nav/backups.ts) and not the icons: the register, the
 * sidebar, the palette and the lists on the side read this.
 */
const OBJECT_OF: Readonly<Record<string, ObjectName>> = {
  // The main bar: the twelve areas.
  overview: "overview",
  apps: "app",
  databases: "database",
  cron: "cron",
  domains: "domain",
  network: "network",
  logs: "log",
  alerts: "alert",
  activity: "activity",
  backups: "backup",
  security: "serviceSecurity",
  server: "server",
  catalog: "recipe",
  settings: "settings",
  // An application's settings: General is the head of the list, the tab they are under.
  "app.settings": "general",
  "app.settings.source": "source",
  "app.settings.build": "build",
  "app.settings.container": "container",
  "app.settings.deploys": "release",
  "app.settings.automation": "automaticDeploys",
  "app.settings.compose": "compose",
  "app.settings.deploy-on-push": "webhook",
  "app.settings.hooks": "hooks",
  "app.settings.previews": "preview",
  "app.settings.resources": "resources",
  "app.settings.processes": "processes",
  "app.settings.mounts": "mounts",
  "app.settings.disk": "disk",
  "app.settings.security": "serviceSecurity",
  "app.settings.access": "access",
  "app.settings.protection": "protection",
  "app.settings.export": "export",
  "app.settings.delete": "delete",
  // Settings.
  "settings.general": "general",
  "settings.fleet": "fleetSettings",
  "settings.registries": "registry",
  "settings.deploy-keys": "key",
  "settings.variables": "variable",
  "settings.secret-managers": "secretManager",
  "settings.console": "console",
  "settings.integrations": "integration",
  "settings.about": "noust",
  "settings.notifications": "channel",
  "settings.log-shipping": "logShipping",
  "settings.tokens": "token",
  "settings.api": "api",
  "settings.manifest": "manifest",
  "settings.mcp": "mcp",
  "settings.accounts": "account",
  "settings.access": "role",
  "settings.sso": "identity",
  "settings.approvals": "approval",
  "settings.audit": "audit",
  "settings.controls": "control",
  "settings.compliance": "report",
  "settings.cloud": "cloud",
  "settings.central": "central",
  // The account.
  account: "account",
  "account.security": "signInSecurity",
};

/**
 * The sections of a resource that the second column draws. They are tabs of the register and so have
 * no icon of their own in a strip (their width is the budget); the column has the room, and draws the
 * object each is about. Apart from the table above so that its ratchet (an object for every area and
 * every section, and nothing else) stays about the areas.
 */
const COLUMN_OBJECT: Readonly<Record<string, ObjectName>> = {
  // An application.
  app: "app",
  "app.deployments": "deployment",
  "app.logs": "log",
  "app.metrics": "overview",
  "app.environment": "variable",
  "app.database": "database",
  "app.domains": "domain",
  "app.settings": "general",
  "app.diagnose": "diagnose",
  "app.terminal": "terminal",
  "app.files": "file",
  // The areas that are a list of routes of their own: the head of each is the area's page, named for what it holds.
  "apps.projects": "project",
  "apps.projects.teams": "team",
  "catalog.installed": "packages",
  "domains.sites": "site",
  "domains.certificates": "certificate",
  "domains.dns": "dns",
  "domains.dns.noust": "routes",
  "alerts.checks": "control",
  "alerts.probes": "probe",
  "alerts.status": "site",
  "alerts.metrics": "overview",
  "backups.schedules": "schedule",
  "backups.volumes": "volume",
  "backups.noust": "noust",
  "backups.destinations": "destination",
  // The machine.
  server: "overview",
  "server.machine": "console",
  "server.processes": "processes",
  "server.monitor": "resources",
  "server.updates": "update",
  "server.services": "service",
  "server.webserver": "site",
  "server.software": "packages",
  "server.files": "file",
  "server.terminal": "terminal",
  "server.central": "central",
  // Network and Security: the head of each is its first section, named for what it holds there.
  network: "port",
  "network.tunnels": "tunnel",
  "network.firewall": "firewall",
  "network.listening": "probe",
  "network.interfaces": "networkInterface",
  security: "control",
  "security.ssh": "key",
  "security.blocks": "protection",
  // The views of the areas that are one page (features/nav/views.ts).
  "logs.view.apps": "app",
  "logs.view.system": "server",
  "activity.view.changes": "activity",
  "activity.view.deployments": "deployment",
  "activity.view.access": "signIn",
  "activity.tasks": "work",
  // What the fleet draws for an area, in "All servers".
  fleet: "overview",
  "fleet.servers": "node",
  "fleet.network": "network",
  "fleet.security": "serviceSecurity",
  "fleet.servers.updates": "update",
  "fleet.servers.software": "packages",
  "fleet.apps": "app",
  "fleet.apps.placements": "node",
  "fleet.certificates": "certificate",
  "fleet.backups": "backup",
  "fleet.activity": "activity",
  "fleet.alerts": "alert",
  // A database, a service and a team.
  database: "overview",
  "database.data": "database",
  "database.keys": "key",
  "database.query": "api",
  "database.backups": "backup",
  "database.users": "access",
  "database.connect": "port",
  "database.metrics": "resources",
  service: "overview",
  "service.logs": "log",
  team: "overview",
  "team.members": "access",
  "team.variables": "variable",
  "team.notifications": "channel",
  "team.settings": "general",
};

/** The ids this table draws: a test holds them to the areas and the sections of the register, in both directions. */
export const ICON_IDS: readonly string[] = Object.keys(OBJECT_OF);

/** What an area or a section is, drawn. One with no object is a typo, and a test says so. */
export function iconOf(entry: Pick<Dest, "id">): LucideIcon {
  const object = OBJECT_OF[entry.id];
  if (object === undefined) throw new Error(`the screen "${entry.id}" has no icon: add its object to features/nav/icons.ts`);
  return OBJECT_ICONS[object];
}

/** The icon of a section in the second column: an area's or a section's own, or the object of a tab of a resource. */
export function columnIconOf(entry: Pick<Dest, "id">): LucideIcon {
  const object = COLUMN_OBJECT[entry.id] ?? OBJECT_OF[entry.id];
  if (object === undefined) throw new Error(`the section "${entry.id}" has no icon in the second column: add its object to features/nav/icons.ts`);
  return OBJECT_ICONS[object];
}

/** The ids the second column draws beside the areas' and the sections': a test holds them to the register. */
export const COLUMN_IDS: readonly string[] = Object.keys(COLUMN_OBJECT);

/** The icon of a screen, or none: for what draws a screen that may be a tab, which has no icon of its own. */
export function sectionIcon(entry: Pick<Dest, "id">): LucideIcon | undefined {
  const object = OBJECT_OF[entry.id];
  return object === undefined ? undefined : OBJECT_ICONS[object];
}
