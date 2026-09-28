/**
 * The console's destinations, written once. The sidebar, the mobile menu, the command
 * palette, the `g` shortcuts and the tab bars all read from here, so a page cannot be
 * reachable from one and missing from another.
 */

import {
  Archive,
  Boxes,
  Clock,
  Cog,
  Database,
  Gauge,
  History,
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

/** The sidebar, top to bottom. Groups are separated by space, not labels. */
export const NAV_GROUPS: readonly (readonly NavItem[])[] = [
  [
    {
      label: "nav.overview.label",
      to: "/",
      icon: Gauge,
      shortcut: { keys: ["g", "o"], description: "nav.overview.goTo" },
      keywords: "nav.overview.keywords",
    },
  ],
  [
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
    { label: "nav.services.label", to: "/services", icon: Cog, keywords: "nav.services.keywords" },
    { label: "nav.cron.label", to: "/cron", icon: Clock, keywords: "nav.cron.keywords" },
  ],
  [
    { label: "nav.domains.label", to: "/domains", icon: ShieldCheck, keywords: "nav.domains.keywords" },
    { label: "nav.backups.label", to: "/backups", icon: Archive, keywords: "nav.backups.keywords" },
  ],
  [
    { label: "nav.activity.label", to: "/activity", icon: History, keywords: "nav.activity.keywords" },
    { label: "nav.server.label", to: "/server", icon: Server, keywords: "nav.server.keywords" },
  ],
];

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

/** An application's sections (D8). Each is a URL under /apps/$domain. */
export const APP_TABS: readonly TabItem[] = [
  { label: "nav.appTabs.overview.label", to: "/apps/$domain", exact: true },
  { label: "nav.appTabs.deployments.label", to: "/apps/$domain/deployments", keywords: "nav.appTabs.deployments.keywords" },
  { label: "nav.appTabs.logs.label", to: "/apps/$domain/logs", keywords: "nav.appTabs.logs.keywords" },
  { label: "nav.appTabs.metrics.label", to: "/apps/$domain/metrics", keywords: "nav.appTabs.metrics.keywords" },
  { label: "nav.appTabs.environment.label", to: "/apps/$domain/environment", keywords: "nav.appTabs.environment.keywords" },
  { label: "nav.appTabs.domains.label", to: "/apps/$domain/domains", keywords: "nav.appTabs.domains.keywords" },
  { label: "nav.appTabs.diagnose.label", to: "/apps/$domain/diagnose", keywords: "nav.appTabs.diagnose.keywords" },
  { label: "nav.appTabs.settings.label", to: "/apps/$domain/settings", keywords: "nav.appTabs.settings.keywords" },
];

export interface SettingsTabItem extends TabItem {
  /** Its name in the command palette, where it stands alone: "Security settings". */
  command: PlainKey;
}

export const SETTINGS_TABS: readonly SettingsTabItem[] = [
  {
    label: "nav.settingsTabs.general.label",
    to: "/settings",
    exact: true,
    keywords: "nav.settingsTabs.general.keywords",
    command: "nav.settingsTabs.general.command",
  },
  {
    label: "nav.settingsTabs.security.label",
    to: "/settings/security",
    keywords: "nav.settingsTabs.security.keywords",
    command: "nav.settingsTabs.security.command",
  },
  {
    label: "nav.settingsTabs.notifications.label",
    to: "/settings/notifications",
    keywords: "nav.settingsTabs.notifications.keywords",
    command: "nav.settingsTabs.notifications.command",
  },
  {
    label: "nav.settingsTabs.integrations.label",
    to: "/settings/integrations",
    keywords: "nav.settingsTabs.integrations.keywords",
    command: "nav.settingsTabs.integrations.command",
  },
  {
    label: "nav.settingsTabs.tokens.label",
    to: "/settings/tokens",
    keywords: "nav.settingsTabs.tokens.keywords",
    command: "nav.settingsTabs.tokens.command",
  },
  {
    label: "nav.settingsTabs.about.label",
    to: "/settings/about",
    keywords: "nav.settingsTabs.about.keywords",
    command: "nav.settingsTabs.about.command",
  },
];
