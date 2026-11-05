import { area, section, shown } from "../../app/navCore";
import type { Dest, More, NavContext, Owner, Strip } from "../../app/navCore";
import type { PlainKey } from "../../i18n";

/** The account's own section is in no group of Settings: it leaves them for /account. */
const NO_GROUP = "";

const settings = area("settings", "/settings", "nav.settings.label", {
  scope: "both",
  group: "pinned",
  shortcut: ["g", "s"],
  goTo: "nav.settings.goTo",
  keywords: "nav.settings.keywords",
  rootLabel: "nav.settingsTabs.overview.label",
  command: "nav.settingsTabs.overview.command",
  owner: "server",
});

/** A section of Settings, in a group of the settings list: what it is for, which is how a person looks for it. */
const setting = (name: string, label: PlainKey, owner: Owner, group: string, more: More = {}): Dest =>
  section(settings, name, label, { owner, ...(group !== NO_GROUP ? { group } : {}), ...more });

/**
 * Settings, in the groups a person looks for a thing by (4.1, item 107): who can do what, where the
 * messages go, what Noust is connected to, how it is kept safe, how a script reaches it, and what
 * Noust is on this machine. Whose a section is (the selected server's, the instance's, the fleet's)
 * is its `owner`, which decides where a node's console sends it, not which group it is under. The
 * register is app/nav.ts; this is one group of it.
 */
export const SETTINGS_SCREENS: Strip = [
  settings,
  // Who may do what, and how they get in.
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
  setting("console", "nav.settingsTabs.console.label", "server", "people", {
    keywords: "nav.settingsTabs.console.keywords",
    command: "nav.settingsTabs.console.command",
  }),
  // What the screens before the console show (item 129): this instance's own, as its sign-in is.
  setting("sign-in-image", "signInImage.nav.label", "instance", "people", {
    central: true,
    keywords: "signInImage.nav.keywords",
    command: "signInImage.nav.command",
  }),
  // Where the messages go, and what is exported. What warns (the rules, the probes) is Alerts, an area of the bar.
  setting("notifications", "nav.settingsTabs.notifications.label", "server", "notices", {
    keywords: "nav.settingsTabs.notifications.keywords",
    command: "nav.settingsTabs.notifications.command",
  }),
  setting("log-shipping", "nav.settingsTabs.logShipping.label", "server", "notices", {
    keywords: "nav.settingsTabs.logShipping.keywords",
    command: "nav.settingsTabs.logShipping.command",
  }),
  // What Noust is connected to: code hosts, image registries, secret managers and cloud providers.
  setting("integrations", "nav.settingsTabs.integrations.label", "server", "connections", {
    keywords: "nav.settingsTabs.integrations.keywords",
    command: "nav.settingsTabs.integrations.command",
  }),
  setting("registries", "registries.nav.label", "server", "connections", {
    keywords: "registries.nav.keywords",
    command: "registries.nav.command",
    permission: "apps.read",
  }),
  setting("deploy-keys", "connections.deployKeys.nav.label", "server", "connections", {
    keywords: "connections.deployKeys.nav.keywords",
    command: "connections.deployKeys.nav.command",
    permission: "apps.read",
  }),
  setting("secret-managers", "variables.providers.nav.label", "server", "connections", {
    keywords: "variables.providers.nav.keywords",
    command: "variables.providers.nav.command",
    permission: "settings.read",
  }),
  setting("cloud", "nav.settingsTabs.cloud.label", "fleet", "connections", {
    central: true,
    keywords: "nav.settingsTabs.cloud.keywords",
    command: "nav.settingsTabs.cloud.command",
    permission: "fleet.manage",
  }),
  // How it is kept safe, and what is recorded.
  setting("compliance", "nav.settingsTabs.compliance.label", "instance", "security", {
    central: true,
    keywords: "nav.settingsTabs.compliance.keywords",
    command: "nav.settingsTabs.compliance.command",
    permission: "compliance.read",
  }),
  setting("controls", "nav.screens.controls.label", "instance", "security", {
    central: true,
    keywords: "nav.screens.controls.keywords",
    command: "nav.screens.controls.command",
    permission: "compliance.read",
  }),
  setting("audit", "audit.nav.label", "instance", "security", {
    central: true,
    keywords: "audit.nav.keywords",
    command: "audit.nav.command",
    permission: "audit.read",
  }),
  // How a script or another program reaches Noust.
  setting("tokens", "nav.settingsTabs.tokens.label", "server", "api", {
    central: true,
    keywords: "nav.settingsTabs.tokens.keywords",
    command: "nav.settingsTabs.tokens.command",
  }),
  setting("api", "apiReference.nav.label", "server", "api", { keywords: "apiReference.nav.keywords", command: "apiReference.nav.command" }),
  setting("manifest", "manifestPreview.nav.label", "server", "api", {
    keywords: "manifestPreview.nav.keywords",
    command: "manifestPreview.nav.command",
    permission: "apps.read",
  }),
  setting("mcp", "mcpSettings.nav.label", "server", "api", {
    keywords: "mcpSettings.nav.keywords",
    command: "mcpSettings.nav.command",
    permission: "settings.read",
  }),
  // Several servers: what they share, how they are added, and how the central is kept.
  setting("fleet", "fleetSettings.nav.label", "fleet", "fleet", {
    central: true,
    when: "fleet",
    keywords: "fleetSettings.nav.keywords",
    command: "fleetSettings.nav.command",
    permission: "fleet.read",
  }),
  setting("central", "nav.settingsTabs.central.label", "fleet", "fleet", {
    central: true,
    when: "fleet",
    keywords: "nav.settingsTabs.central.keywords",
    command: "nav.settingsTabs.central.command",
  }),
  // Noust on this machine: how it is set up, what it shares with the apps, and which version it is.
  setting("general", "nav.settingsTabs.general.label", "server", "noust", {
    keywords: "nav.settingsTabs.general.keywords",
    command: "nav.settingsTabs.general.command",
  }),
  setting("variables", "variables.nav.settings.label", "server", "noust", {
    keywords: "variables.nav.settings.keywords",
    command: "variables.nav.settings.command",
    permission: "settings.read",
  }),
  setting("about", "nav.settingsTabs.about.label", "server", "noust", {
    keywords: "nav.settingsTabs.about.keywords",
    command: "nav.settingsTabs.about.command",
  }),
];

/**
 * The groups of the list, in the order they are drawn. The ids are titled by whoever draws them
 * (`nav.settingsGroups.titles`). The budget is eight sections a group and eight groups, which is the
 * two lists of 4.0 (the server's and the instance's) drawn as one: a person looks for a setting by
 * what it is for, not by whose it is.
 */
export const SETTINGS_GROUPS: readonly string[] = ["people", "notices", "connections", "security", "api", "fleet", "noust"];

/** The group of a section: the root is the hub that lists them all, and is in none. */
export function settingsGroupOf(entry: Dest): string | undefined {
  return entry.id === "settings" ? undefined : entry.group;
}

/**
 * The sections of Settings, in titled groups. `screens` is the group of screens Settings is written
 * as (SETTINGS_SCREENS): the page that draws the list passes it, so its route is given the texts of
 * those sections and no others. The account's own sections are not Settings (NV-7): they leave for
 * /account, so no group holds them.
 */
export function groupSettings(screens: Strip, ctx: NavContext = {}): { id: string; sections: Dest[] }[] {
  const entries = screens.filter((entry) => entry.kind === "section" && shown(entry, ctx) && entry.owner !== undefined);
  return SETTINGS_GROUPS.flatMap((id) => {
    const sections = entries.filter((entry) => settingsGroupOf(entry) === id);
    return sections.length > 0 ? [{ id, sections }] : [];
  });
}

/**
 * What the screens that changed their name were called before, as palette search words (NV-5):
 * an operator who knew "Console access" still finds it. Keys, so the old name is translated.
 */
export const RENAMED: Readonly<Record<string, readonly PlainKey[]>> = {
  // The areas of the bar that 4.1 renamed or moved: what they were called in 4.0.
  overview: ["nav.renamed.overview"],
  cron: ["nav.renamed.cron"],
  "databases.engines": ["nav.renamed.engines"],
  backups: ["nav.renamed.backups"],
  server: ["nav.renamed.server"],
  // The updates of 4.0 are a section of the server again (4.2); "System updates" typed whole still opens it, ahead of the alert rule that names them.
  "server.updates": ["nav.renamed.systemUpdates"],
  // What 4.1 had under the Server and under Domains, and 4.2 moved to areas of their own, and Settings > Servers, which is the fleet's list.
  security: ["nav.renamed.serverSecurity"],
  "network.listening": ["nav.renamed.serverNetwork"],
  network: ["nav.renamed.domainsPorts"],
  "network.tunnels": ["nav.renamed.domainsTunnels"],
  "fleet.servers": ["nav.renamed.settingsServers"],
  domains: ["nav.renamed.domains"],
  "domains.sites": ["nav.renamed.webServerSites"],
  catalog: ["nav.renamed.recipes"],
  "catalog.installed": ["nav.renamed.recipesInstalled"],
  "apps.projects": ["nav.renamed.projects"],
  alerts: ["nav.renamed.alerts"],
  "alerts.probes": ["nav.renamed.probes"],
  "settings.notifications": ["nav.renamed.notifications"],
  "settings.console": ["nav.renamed.console"],
  "settings.about": ["nav.renamed.about"],
  "settings.access": ["nav.renamed.access"],
  "settings.compliance": ["nav.renamed.compliance"],
  "settings.log-shipping": ["nav.renamed.logShipping"],
  "settings.cloud": ["nav.renamed.cloud"],
  "settings.integrations": ["nav.renamed.integrations"],
  "settings.accounts": ["nav.renamed.accounts"],
  "settings.variables": ["nav.renamed.variables"],
  "settings.general": ["nav.renamed.general"],
  "account.security": ["nav.renamed.security"],
};
