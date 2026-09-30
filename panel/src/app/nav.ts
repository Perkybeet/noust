/**
 * The console's destinations, written once. The sidebar, the mobile menu, the command
 * palette, the `g` shortcuts and the tab bars all read from here, so a page cannot be
 * reachable from one and missing from another.
 */

import {
  Archive,
  Boxes,
  Clock,
  Database,
  Gauge,
  History,
  Network,
  Server,
  Settings,
  ShieldCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { PlainKey } from "../i18n";
import type { FileRouteTypes } from "../routeTree.gen";

/** Every path of the console, as the router knows it. */
export type ConsolePath = FileRouteTypes["to"];

/**
 * Labels and search words are catalog keys (i18n/en/nav.ts), not text: whoever renders an
 * item translates it with `t(item.label)`, so every place that shows a destination speaks
 * the active language.
 */
export interface NavItem {
  label: PlainKey;
  to: ConsolePath;
  icon: LucideIcon;
  /** The two-key shortcut that opens it, such as ["g", "a"], and what the shortcut list calls it. */
  shortcut?: { keys: readonly string[]; description: PlainKey };
  /** Other words an operator might type for it in the palette. */
  keywords?: PlainKey;
}

/**
 * A server's destinations in the sidebar, top to bottom: what it runs, then the machine
 * itself. Groups are separated by space; on a fleet the sidebar heads them with the server's
 * name, since every one of them is that server's. Services live under Server (its tab).
 */
export const NAV_GROUPS: readonly (readonly NavItem[])[] = [
  [
    {
      label: "nav.overview.label",
      to: "/",
      icon: Gauge,
      shortcut: { keys: ["g", "o"], description: "nav.overview.goTo" },
      keywords: "nav.overview.keywords",
    },
    {
      label: "nav.apps.label",
      to: "/apps",
      icon: Boxes,
      shortcut: { keys: ["g", "a"], description: "nav.apps.goTo" },
      keywords: "nav.apps.keywords",
    },
    {
      label: "nav.databases.label",
      to: "/databases",
      icon: Database,
      shortcut: { keys: ["g", "b"], description: "nav.databases.goTo" },
      keywords: "nav.databases.keywords",
    },
    { label: "nav.domains.label", to: "/domains", icon: ShieldCheck, keywords: "nav.domains.keywords" },
    { label: "nav.backups.label", to: "/backups", icon: Archive, keywords: "nav.backups.keywords" },
  ],
  [
    { label: "nav.server.label", to: "/server", icon: Server, keywords: "nav.server.keywords" },
    { label: "nav.cron.label", to: "/cron", icon: Clock, keywords: "nav.cron.keywords" },
    { label: "nav.activity.label", to: "/activity", icon: History, keywords: "nav.activity.keywords" },
  ],
];

/**
 * The fleet, every server at once ("All servers"): in the sidebar of a central that manages
 * servers, or of a hub; a lone server reaches it from Settings > Servers.
 */
export const FLEET_ITEM: NavItem = {
  label: "servers.nav.fleet.label",
  to: "/fleet",
  icon: Network,
  shortcut: { keys: ["g", "f"], description: "servers.nav.fleet.goTo" },
  keywords: "servers.nav.fleet.keywords",
};

export const SETTINGS_ITEM: NavItem = {
  label: "nav.settings.label",
  to: "/settings",
  icon: Settings,
  shortcut: { keys: ["g", "s"], description: "nav.settings.goTo" },
  keywords: "nav.settings.keywords",
};

export interface TabItem {
  label: PlainKey;
  to: ConsolePath;
  /** Active only on this exact path (the first tab), not on paths beneath it. */
  exact?: boolean;
  keywords?: PlainKey;
}

/**
 * An application's sections (D8), eight at most (docs/DESIGN.md, "LinkTabs"). Each is a URL
 * under /apps/$domain. Diagnose is one too, but off the strip: it is reached from what is wrong
 * (the status banner, the Overview's "Needs attention") and from the header's "More actions".
 */
export const APP_TABS: readonly TabItem[] = [
  { label: "nav.appTabs.overview.label", to: "/apps/$domain", exact: true },
  { label: "nav.appTabs.deployments.label", to: "/apps/$domain/deployments", keywords: "nav.appTabs.deployments.keywords" },
  { label: "nav.appTabs.logs.label", to: "/apps/$domain/logs", keywords: "nav.appTabs.logs.keywords" },
  { label: "nav.appTabs.metrics.label", to: "/apps/$domain/metrics", keywords: "nav.appTabs.metrics.keywords" },
  { label: "nav.appTabs.environment.label", to: "/apps/$domain/environment", keywords: "nav.appTabs.environment.keywords" },
  { label: "databases.appTab.tabLabel", to: "/apps/$domain/database", keywords: "databases.appTab.tabKeywords" },
  { label: "nav.appTabs.domains.label", to: "/apps/$domain/domains", keywords: "nav.appTabs.domains.keywords" },
  { label: "nav.appTabs.settings.label", to: "/apps/$domain/settings", keywords: "nav.appTabs.settings.keywords" },
];

/**
 * Whose a settings section is. `server`: the selected server's own (`/n/web-2/settings/...`
 * on a node). `central`: the central's, whichever server is selected - a node refuses a central
 * on its sign-in, tokens and two-factor on purpose, so these can only ever be the central's.
 */
export type SettingsScope = "server" | "central";

export interface SettingsTabItem extends TabItem {
  /** Its name in the command palette, where it stands alone: "Security settings". */
  command: PlainKey;
  scope: SettingsScope;
  /** Only where there is a fleet to hold (a central with servers, or a hub): the seal. */
  fleetOnly?: boolean;
  /** Only for whoever holds this permission (`accounts.read`, `audit.read`...). */
  permission?: string;
}

/**
 * The settings sections, the selected server's first, then the central's. Every `central`
 * section's path is also in nodeRoute.ts's CENTRAL_PATHS, which keeps the node off it.
 */
export const SETTINGS_TABS: readonly SettingsTabItem[] = [
  {
    label: "nav.settingsTabs.general.label",
    to: "/settings",
    exact: true,
    keywords: "nav.settingsTabs.general.keywords",
    command: "nav.settingsTabs.general.command",
    scope: "server",
  },
  {
    label: "nav.settingsTabs.notifications.label",
    to: "/settings/notifications",
    keywords: "nav.settingsTabs.notifications.keywords",
    command: "nav.settingsTabs.notifications.command",
    scope: "server",
  },
  {
    label: "nav.settingsTabs.integrations.label",
    to: "/settings/integrations",
    keywords: "nav.settingsTabs.integrations.keywords",
    command: "nav.settingsTabs.integrations.command",
    scope: "server",
  },
  {
    label: "nav.settingsTabs.about.label",
    to: "/settings/about",
    keywords: "nav.settingsTabs.about.keywords",
    command: "nav.settingsTabs.about.command",
    scope: "server",
  },
  {
    label: "servers.nav.settingsTab.label",
    to: "/settings/servers",
    keywords: "servers.nav.settingsTab.keywords",
    command: "servers.nav.settingsTab.command",
    scope: "central",
  },
  {
    label: "accounts.nav.label",
    to: "/settings/accounts",
    keywords: "accounts.nav.keywords",
    command: "accounts.nav.command",
    scope: "central",
    permission: "accounts.read",
  },
  {
    label: "nav.settingsTabs.security.label",
    to: "/settings/security",
    keywords: "nav.settingsTabs.security.keywords",
    command: "nav.settingsTabs.security.command",
    scope: "central",
  },
  {
    label: "nav.settingsTabs.tokens.label",
    to: "/settings/tokens",
    keywords: "nav.settingsTabs.tokens.keywords",
    command: "nav.settingsTabs.tokens.command",
    scope: "central",
  },
  {
    label: "approvals.nav.label",
    to: "/settings/approvals",
    keywords: "approvals.nav.keywords",
    command: "approvals.nav.command",
    scope: "central",
  },
  {
    label: "audit.nav.label",
    to: "/settings/audit",
    keywords: "audit.nav.keywords",
    command: "audit.nav.command",
    scope: "central",
    permission: "audit.read",
  },
  {
    label: "compliance.nav.label",
    to: "/settings/compliance",
    keywords: "compliance.nav.keywords",
    command: "compliance.nav.command",
    scope: "central",
    permission: "compliance.read",
  },
  {
    label: "nav.settingsTabs.central.label",
    to: "/settings/central",
    keywords: "nav.settingsTabs.central.keywords",
    command: "nav.settingsTabs.central.command",
    scope: "central",
    fleetOnly: true,
  },
];

/** The fleet's views, each a URL under /fleet: every server at once. */
export interface FleetTabItem extends TabItem {
  /** Its name in the command palette, where it stands alone: "Certificates on every server". */
  command: PlainKey;
}

export const FLEET_TABS: readonly FleetTabItem[] = [
  { label: "fleet.tabs.summary", to: "/fleet", exact: true, keywords: "fleet.tabs.keywords.summary", command: "fleet.tabs.command.summary" },
  { label: "fleet.tabs.servers", to: "/fleet/servers", keywords: "fleet.tabs.keywords.servers", command: "fleet.tabs.command.servers" },
  { label: "fleet.tabs.apps", to: "/fleet/apps", keywords: "fleet.tabs.keywords.apps", command: "fleet.tabs.command.apps" },
  {
    label: "fleet.tabs.certificates",
    to: "/fleet/certificates",
    keywords: "fleet.tabs.keywords.certificates",
    command: "fleet.tabs.command.certificates",
  },
  { label: "fleet.tabs.backups", to: "/fleet/backups", keywords: "fleet.tabs.keywords.backups", command: "fleet.tabs.command.backups" },
  { label: "fleet.tabs.updates", to: "/fleet/updates", keywords: "fleet.tabs.keywords.updates", command: "fleet.tabs.command.updates" },
  { label: "fleet.tabs.activity", to: "/fleet/activity", keywords: "fleet.tabs.keywords.activity", command: "fleet.tabs.command.activity" },
];
