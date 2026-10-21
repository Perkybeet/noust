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
  // The bar of a server.
  overview: "overview",
  projects: "project",
  apps: "app",
  databases: "database",
  domains: "domain",
  backups: "backup",
  cron: "cron",
  logs: "log",
  activity: "activity",
  server: "server",
  settings: "settings",
  // The bar of the fleet.
  fleet: "overview",
  "fleet.servers": "node",
  "fleet.apps": "app",
  "fleet.certificates": "certificate",
  "fleet.backups": "backup",
  "fleet.updates": "update",
  "fleet.activity": "activity",
  // An application's settings: General is the head of the list, the tab they are under.
  "app.settings": "general",
  "app.settings.source": "source",
  "app.settings.build": "build",
  "app.settings.builds": "builds",
  "app.settings.container": "container",
  "app.settings.deploys": "release",
  "app.settings.deploy-on-push": "trigger",
  "app.settings.hooks": "hook",
  "app.settings.previews": "preview",
  "app.settings.resources": "resources",
  "app.settings.disk": "disk",
  "app.settings.processes": "process",
  "app.settings.mounts": "mount",
  "app.settings.security": "security",
  "app.settings.access": "access",
  "app.settings.export": "export",
  "app.settings.delete": "delete",
  // Settings.
  "settings.variables": "variable",
  "settings.secret-managers": "secretManager",
  "settings.console": "console",
  "settings.integrations": "integration",
  "settings.about": "noust",
  "settings.notifications": "channel",
  "settings.alerts": "alert",
  "settings.probes": "probe",
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
  "settings.servers": "node",
  "settings.cloud": "cloud",
  "settings.central": "central",
  // The account.
  account: "account",
  "account.security": "signIn",
};

/** The ids this table draws: a test holds them to the areas and the sections of the register, in both directions. */
export const ICON_IDS: readonly string[] = Object.keys(OBJECT_OF);

/** What an area or a section is, drawn. One with no object is a typo, and a test says so. */
export function iconOf(entry: Pick<Dest, "id">): LucideIcon {
  const object = OBJECT_OF[entry.id];
  if (object === undefined) throw new Error(`the screen "${entry.id}" has no icon: add its object to features/nav/icons.ts`);
  return OBJECT_ICONS[object];
}

/** The icon of a screen, or none: for what draws a screen that may be a tab, which has no icon of its own. */
export function sectionIcon(entry: Pick<Dest, "id">): LucideIcon | undefined {
  const object = OBJECT_OF[entry.id];
  return object === undefined ? undefined : OBJECT_ICONS[object];
}
