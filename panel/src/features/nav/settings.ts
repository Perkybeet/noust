import { Settings } from "lucide-react";

import { area, isStaged, section, tabItem } from "../../app/navCore";
import type { Dest, More, Owner, Strip, TabItem } from "../../app/navCore";
import type { PlainKey } from "../../i18n";

/** The account's own section is in no group of Settings: it leaves them for /account. */
const NO_GROUP = "";

const settings = area("settings", "/settings", "nav.settings.label", Settings, {
  scope: "both",
  group: "pinned",
  shortcut: ["g", "s"],
  goTo: "nav.settings.goTo",
  keywords: "nav.settings.keywords",
  rootLabel: "nav.settingsTabs.general.label",
  command: "nav.settingsTabs.general.command",
  owner: "server",
});

/** A section of Settings, in a group of the settings list that says whose it is (the groups are titled by whoever draws them). */
const setting = (name: string, label: PlainKey, owner: Owner, group: string, more: More = {}): Dest =>
  section(settings, name, label, { owner, ...(group !== NO_GROUP ? { group } : {}), ...more });

/**
 * Settings, the selected server's own sections first and then the central's, which carry `central`:
 * nodeRoute.ts keeps a node off them. The register is app/nav.ts; this is one group of it.
 */
export const SETTINGS_SCREENS: Strip = [
  settings,
  setting("notifications", "nav.settingsTabs.notifications.label", "server", "monitoring", {
    keywords: "nav.settingsTabs.notifications.keywords",
    command: "nav.settingsTabs.notifications.command",
  }),
  setting("alerts", "nav.settingsTabs.alerts.label", "server", "monitoring", {
    keywords: "nav.settingsTabs.alerts.keywords",
    command: "nav.settingsTabs.alerts.command",
  }),
  setting("integrations", "nav.settingsTabs.integrations.label", "server", "this-server", {
    keywords: "nav.settingsTabs.integrations.keywords",
    command: "nav.settingsTabs.integrations.command",
  }),
  setting("probes", "probes.nav.label", "server", "monitoring", { keywords: "probes.nav.keywords", command: "probes.nav.command" }),
  setting("log-shipping", "runtime.logShipping.navLabel", "server", "monitoring", {
    keywords: "runtime.logShipping.navKeywords",
    command: "runtime.logShipping.navCommand",
  }),
  setting("variables", "variables.nav.settings.label", "server", "this-server", {
    keywords: "variables.nav.settings.keywords",
    command: "variables.nav.settings.command",
    permission: "settings.read",
  }),
  setting("secret-managers", "variables.providers.nav.label", "server", "this-server", {
    keywords: "variables.providers.nav.keywords",
    command: "variables.providers.nav.command",
    permission: "settings.read",
  }),
  setting("console", "consoleAccess.nav.label", "server", "this-server", { keywords: "consoleAccess.nav.keywords", command: "consoleAccess.nav.command" }),
  setting("api", "apiReference.nav.label", "server", "developers", { keywords: "apiReference.nav.keywords", command: "apiReference.nav.command" }),
  setting("manifest", "manifestPreview.nav.label", "server", "developers", {
    keywords: "manifestPreview.nav.keywords",
    command: "manifestPreview.nav.command",
    permission: "apps.read",
  }),
  setting("mcp", "mcpSettings.nav.label", "server", "developers", {
    keywords: "mcpSettings.nav.keywords",
    command: "mcpSettings.nav.command",
    permission: "settings.read",
  }),
  setting("about", "nav.settingsTabs.about.label", "server", "this-server", {
    keywords: "nav.settingsTabs.about.keywords",
    command: "nav.settingsTabs.about.command",
  }),
  setting("servers", "servers.nav.settingsTab.label", "fleet", "fleet", {
    central: true,
    keywords: "servers.nav.settingsTab.keywords",
    command: "servers.nav.settingsTab.command",
  }),
  setting("cloud", "cloud.nav.label", "fleet", "fleet", {
    central: true,
    keywords: "cloud.nav.keywords",
    command: "cloud.nav.command",
    permission: "fleet.manage",
  }),
  setting("accounts", "accounts.nav.label", "instance", "people", {
    central: true,
    keywords: "accounts.nav.keywords",
    command: "accounts.nav.command",
    permission: "accounts.read",
  }),
  // The account's own security leaves Settings for /account/security; until it does it is still a section.
  setting("security", "nav.settingsTabs.security.label", "account", NO_GROUP, {
    central: true,
    keywords: "nav.settingsTabs.security.keywords",
    command: "nav.settingsTabs.security.command",
  }),
  setting("access", "access.nav.label", "instance", "people", {
    central: true,
    keywords: "access.nav.keywords",
    command: "access.nav.command",
    permission: "accounts.read",
  }),
  setting("sso", "ssoAdmin.nav.label", "instance", "people", {
    central: true,
    keywords: "ssoAdmin.nav.keywords",
    command: "ssoAdmin.nav.command",
    permission: "accounts.read",
  }),
  setting("tokens", "nav.settingsTabs.tokens.label", "server", "developers", {
    central: true,
    keywords: "nav.settingsTabs.tokens.keywords",
    command: "nav.settingsTabs.tokens.command",
  }),
  setting("approvals", "approvals.nav.label", "instance", "people", {
    central: true,
    keywords: "approvals.nav.keywords",
    command: "approvals.nav.command",
  }),
  setting("audit", "audit.nav.label", "instance", "governance", {
    central: true,
    keywords: "audit.nav.keywords",
    command: "audit.nav.command",
    permission: "audit.read",
  }),
  setting("compliance", "compliance.nav.label", "instance", "governance", {
    central: true,
    keywords: "compliance.nav.keywords",
    command: "compliance.nav.command",
    permission: "compliance.read",
  }),
  setting("central", "nav.settingsTabs.central.label", "fleet", "fleet", {
    central: true,
    when: "fleet",
    keywords: "nav.settingsTabs.central.keywords",
    command: "nav.settingsTabs.central.command",
  }),
  // New in 4.0, and a route before it is a page (INTERIM): frameworks, controls and the security profile.
  setting("controls", "nav.screens.controls.label", "instance", "governance", {
    central: true,
    keywords: "nav.screens.controls.keywords",
    command: "nav.screens.controls.command",
    permission: "compliance.read",
  }),
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

/** The settings sections as the list on the side draws them: the screens that are only a route so far are not offered. */
export const SETTINGS_TABS: readonly SettingsTabItem[] = SETTINGS_SCREENS.filter((entry) => !isStaged(entry)).map((entry, index): SettingsTabItem => {
  if (entry.command === undefined) throw new Error(`the settings section "${entry.id}" has no palette phrase`);
  return {
    ...tabItem(entry, index === 0),
    command: entry.command,
    scope: entry.central === true ? "central" : "server",
    ...(entry.when === "fleet" ? { fleetOnly: true } : {}),
    ...(entry.permission !== undefined ? { permission: entry.permission } : {}),
  };
});
