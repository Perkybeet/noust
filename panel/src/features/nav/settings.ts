import { Settings } from "lucide-react";

import { area, section, shown } from "../../app/navCore";
import type { Dest, More, NavContext, Owner, Strip } from "../../app/navCore";
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
 * Settings: the selected server's own sections first, in three groups, and then the instance's,
 * which carry `central` (nodeRoute.ts keeps a node off them). Within a group the order is the one
 * an operator sets a server up in. The register is app/nav.ts; this is one group of it.
 */
export const SETTINGS_SCREENS: Strip = [
  settings,
  // This server: what it is and how it is reached.
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
  setting("console", "nav.settingsTabs.console.label", "server", "this-server", {
    keywords: "nav.settingsTabs.console.keywords",
    command: "nav.settingsTabs.console.command",
  }),
  setting("integrations", "nav.settingsTabs.integrations.label", "server", "this-server", {
    keywords: "nav.settingsTabs.integrations.keywords",
    command: "nav.settingsTabs.integrations.command",
  }),
  setting("about", "nav.settingsTabs.about.label", "server", "this-server", {
    keywords: "nav.settingsTabs.about.keywords",
    command: "nav.settingsTabs.about.command",
  }),
  // Monitoring: where the messages go, when they are sent, what is watched from outside, what is exported.
  setting("notifications", "nav.settingsTabs.notifications.label", "server", "monitoring", {
    keywords: "nav.settingsTabs.notifications.keywords",
    command: "nav.settingsTabs.notifications.command",
  }),
  setting("alerts", "nav.settingsTabs.alerts.label", "server", "monitoring", {
    keywords: "nav.settingsTabs.alerts.keywords",
    command: "nav.settingsTabs.alerts.command",
  }),
  setting("probes", "nav.settingsTabs.probes.label", "server", "monitoring", {
    keywords: "nav.settingsTabs.probes.keywords",
    command: "nav.settingsTabs.probes.command",
  }),
  setting("log-shipping", "nav.settingsTabs.logShipping.label", "server", "monitoring", {
    keywords: "nav.settingsTabs.logShipping.keywords",
    command: "nav.settingsTabs.logShipping.command",
  }),
  // Developers: the ways in for scripts and other programs.
  setting("tokens", "nav.settingsTabs.tokens.label", "server", "developers", {
    central: true,
    keywords: "nav.settingsTabs.tokens.keywords",
    command: "nav.settingsTabs.tokens.command",
  }),
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
  // The instance's: who may do what and what is recorded, then the fleet this central holds.
  setting("accounts", "accounts.nav.label", "instance", "people", {
    central: true,
    keywords: "accounts.nav.keywords",
    command: "accounts.nav.command",
    permission: "accounts.read",
  }),
  setting("access", "nav.settingsTabs.access.label", "instance", "people", {
    central: true,
    keywords: "nav.settingsTabs.access.keywords",
    command: "nav.settingsTabs.access.command",
    permission: "accounts.read",
  }),
  setting("sso", "ssoAdmin.nav.label", "instance", "people", {
    central: true,
    keywords: "ssoAdmin.nav.keywords",
    command: "ssoAdmin.nav.command",
    permission: "accounts.read",
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
  setting("controls", "nav.screens.controls.label", "instance", "governance", {
    central: true,
    keywords: "nav.screens.controls.keywords",
    command: "nav.screens.controls.command",
    permission: "compliance.read",
  }),
  setting("compliance", "nav.settingsTabs.compliance.label", "instance", "governance", {
    central: true,
    keywords: "nav.settingsTabs.compliance.keywords",
    command: "nav.settingsTabs.compliance.command",
    permission: "compliance.read",
  }),
  setting("servers", "servers.nav.settingsTab.label", "fleet", "fleet", {
    central: true,
    keywords: "servers.nav.settingsTab.keywords",
    command: "servers.nav.settingsTab.command",
  }),
  setting("cloud", "nav.settingsTabs.cloud.label", "fleet", "fleet", {
    central: true,
    keywords: "nav.settingsTabs.cloud.keywords",
    command: "nav.settingsTabs.cloud.command",
    permission: "fleet.manage",
  }),
  setting("central", "nav.settingsTabs.central.label", "fleet", "fleet", {
    central: true,
    when: "fleet",
    keywords: "nav.settingsTabs.central.keywords",
    command: "nav.settingsTabs.central.command",
  }),
];

/** The block of Settings a section is in: the selected server's own, or the instance's (NV-7). */
export type SettingsBlock = "server" | "instance";

/** The groups of each block, in the order they are drawn. The ids are titled by whoever draws them. */
export const SETTINGS_GROUPS: Readonly<Record<SettingsBlock, readonly string[]>> = {
  server: ["this-server", "monitoring", "developers"],
  instance: ["people", "governance", "fleet"],
};

const BLOCK_OWNERS: Readonly<Record<SettingsBlock, readonly Owner[]>> = { server: ["server"], instance: ["instance", "fleet"] };

/** The group of a section: General, which is Settings' own page, is the first of "This server". */
export function settingsGroupOf(entry: Dest): string {
  return entry.id === "settings" || entry.group === undefined || entry.group === "" ? "this-server" : entry.group;
}

/**
 * The sections of Settings a block holds, in titled groups. `screens` is the group of screens
 * Settings is written as (SETTINGS_SCREENS): the page that draws the list passes it, so its route
 * is given the texts of those sections and no others. The account's own sections are not Settings
 * (NV-7): they leave for /account, so no block holds them.
 */
export function groupSettings(screens: Strip, block: SettingsBlock, ctx: NavContext = {}): { id: string; sections: Dest[] }[] {
  const entries = screens.filter((entry) => (entry.id === "settings" || entry.kind === "section") && shown(entry, ctx) && entry.owner !== undefined && BLOCK_OWNERS[block].includes(entry.owner));
  return SETTINGS_GROUPS[block].flatMap((id) => {
    const sections = entries.filter((entry) => settingsGroupOf(entry) === id);
    return sections.length > 0 ? [{ id, sections }] : [];
  });
}

/**
 * What the screens that changed their name were called before, as palette search words (NV-5):
 * an operator who knew "Console access" still finds it. Keys, so the old name is translated.
 */
export const RENAMED: Readonly<Record<string, readonly PlainKey[]>> = {
  domains: ["nav.renamed.domains"],
  "apps.recipes": ["nav.renamed.catalog"],
  "settings.notifications": ["nav.renamed.notifications"],
  "settings.alerts": ["nav.renamed.alerts"],
  "settings.console": ["nav.renamed.console"],
  "settings.about": ["nav.renamed.about"],
  "settings.access": ["nav.renamed.access"],
  "settings.compliance": ["nav.renamed.compliance"],
  "settings.probes": ["nav.renamed.probes"],
  "settings.log-shipping": ["nav.renamed.logShipping"],
  "settings.cloud": ["nav.renamed.cloud"],
  "account.security": ["nav.renamed.security"],
};
