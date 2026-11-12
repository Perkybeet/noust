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
 * Noust is as the central keeps it. Whose a section is (the instance's, the fleet's, a server's) is its
 * `owner`, not which group it is under. Since 4.3 (item 166) the sections are the central's and the server
 * chosen in the selector does not count in them: what the central carries to every server is written once
 * in the section it belongs to, with the servers that follow it, and what is still each server's own is a
 * table of servers. The register is app/nav.ts; this is one group of it.
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
    hint: "ssoAdmin.nav.hint",
    keywords: "ssoAdmin.nav.keywords",
    command: "ssoAdmin.nav.command",
    permission: "accounts.read",
  }),
  setting("access-security", "nav.settingsTabs.accessSecurity.label", "instance", "people", {
    central: true,
    keywords: "nav.settingsTabs.accessSecurity.keywords",
    command: "nav.settingsTabs.accessSecurity.command",
    permission: "settings.read",
  }),
  setting("approvals", "approvals.nav.label", "instance", "people", {
    central: true,
    keywords: "approvals.nav.keywords",
    command: "approvals.nav.command",
  }),
  // Where the messages go. What warns (the rules, the probes) is Alerts, an area of the bar; what is copied out is a connection.
  // The channels are the central's and every server it manages follows them (item 166): the selector does not count here.
  setting("notifications", "nav.settingsTabs.notifications.label", "fleet", "notices", {
    central: true,
    keywords: "nav.settingsTabs.notifications.keywords",
    command: "nav.settingsTabs.notifications.command",
  }),
  // What Noust is connected to: code hosts, where logs are copied to, image registries, secret managers and cloud providers.
  // The code hosts, the log shipping, the secret managers and the deploy keys are each server's own: on the central they are
  // a table of servers (`table`), and with a server chosen they are that server's page.
  setting("integrations", "nav.settingsTabs.integrations.label", "server", "connections", {
    table: true,
    keywords: "nav.settingsTabs.integrations.keywords",
    command: "nav.settingsTabs.integrations.command",
  }),
  setting("log-shipping", "nav.settingsTabs.logShipping.label", "server", "connections", {
    table: true,
    keywords: "nav.settingsTabs.logShipping.keywords",
    command: "nav.settingsTabs.logShipping.command",
  }),
  setting("registries", "registries.nav.label", "fleet", "connections", {
    central: true,
    keywords: "registries.nav.keywords",
    command: "registries.nav.command",
    permission: "apps.read",
  }),
  setting("deploy-keys", "connections.deployKeys.nav.label", "server", "connections", {
    table: true,
    keywords: "connections.deployKeys.nav.keywords",
    command: "connections.deployKeys.nav.command",
    permission: "apps.read",
  }),
  setting("cloud", "nav.settingsTabs.cloud.label", "fleet", "connections", {
    central: true,
    keywords: "nav.settingsTabs.cloud.keywords",
    command: "nav.settingsTabs.cloud.command",
    permission: "fleet.manage",
  }),
  setting("secret-managers", "variables.providers.nav.label", "server", "connections", {
    table: true,
    keywords: "variables.providers.nav.keywords",
    command: "variables.providers.nav.command",
    permission: "settings.read",
  }),
  // What a rule book asks of this Noust (the frameworks, the controls they are made of, their report), and what is recorded.
  setting("compliance", "nav.settingsTabs.compliance.label", "instance", "security", {
    central: true,
    keywords: "nav.settingsTabs.compliance.keywords",
    command: "nav.settingsTabs.compliance.command",
    permission: "settings.read",
  }),
  setting("audit", "audit.nav.label", "instance", "security", {
    central: true,
    keywords: "audit.nav.keywords",
    command: "audit.nav.command",
    permission: "audit.read",
  }),
  // How a script or another program reaches Noust.
  setting("tokens", "nav.settingsTabs.tokens.label", "instance", "api", {
    central: true,
    keywords: "nav.settingsTabs.tokens.keywords",
    command: "nav.settingsTabs.tokens.command",
  }),
  setting("api", "apiReference.nav.label", "instance", "api", { central: true, keywords: "apiReference.nav.keywords", command: "apiReference.nav.command" }),
  // The manifest is a machine written down in a file: it is a page of the machine (Server > Manifest), not a setting.
  setting("mcp", "mcpSettings.nav.label", "instance", "api", {
    central: true,
    keywords: "mcpSettings.nav.keywords",
    command: "mcpSettings.nav.command",
    permission: "settings.read",
  }),
  // Noust as the central keeps it: what it decides for every server, the address its notices open, what it shows before
  // anyone signs in, what it shares with the applications, how the central is kept and which version it is. What is the
  // machine's (its folders, its web server, publishing its console) is Server.
  setting("general", "nav.settingsTabs.general.label", "fleet", "noust", {
    central: true,
    keywords: "nav.settingsTabs.general.keywords",
    command: "nav.settingsTabs.general.command",
  }),
  setting("console", "nav.settingsTabs.console.label", "fleet", "noust", {
    central: true,
    keywords: "nav.settingsTabs.console.keywords",
    command: "nav.settingsTabs.console.command",
  }),
  // What the screens before the console show (item 129): this instance's own, as its sign-in is, and no fleet's.
  setting("sign-in-image", "signInImage.nav.label", "instance", "noust", {
    central: true,
    keywords: "signInImage.nav.keywords",
    command: "signInImage.nav.command",
  }),
  setting("variables", "variables.nav.settings.label", "fleet", "noust", {
    central: true,
    keywords: "variables.nav.settings.keywords",
    command: "variables.nav.settings.command",
    permission: "settings.read",
  }),
  setting("central", "nav.settingsTabs.central.label", "fleet", "noust", {
    central: true,
    when: "fleet",
    keywords: "nav.settingsTabs.central.keywords",
    command: "nav.settingsTabs.central.command",
  }),
  setting("about", "nav.settingsTabs.about.label", "instance", "noust", {
    central: true,
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
export const SETTINGS_GROUPS: readonly string[] = ["people", "notices", "connections", "security", "api", "noust"];

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
  // The server's section of 4.1 that was "This server" is Machine; Brute force is Blocks; and the fleet's Updates, a list of its own, is the servers that have one.
  "server.machine": ["nav.renamed.thisServer"],
  "security.blocks": ["nav.renamed.bruteForce"],
  "fleet.servers.updates": ["nav.renamed.fleetUpdates"],
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
  // 4.3: Fleet settings is not a page; what it held is in the section each kind belongs to, which still answers to its old name.
  alerts: ["nav.renamed.alerts", "nav.renamed.fleetSettings"],
  "alerts.probes": ["nav.renamed.probes"],
  "backups.destinations": ["nav.renamed.fleetSettings"],
  "server.manifest": ["nav.renamed.settingsManifest"],
  "settings.registries": ["nav.renamed.fleetSettings"],
  "settings.notifications": ["nav.renamed.notifications", "nav.renamed.channels", "nav.renamed.fleetSettings"],
  "settings.console": ["nav.renamed.console", "nav.renamed.fleetSettings"],
  "settings.about": ["nav.renamed.about"],
  "settings.access": ["nav.renamed.access"],
  "settings.compliance": ["nav.renamed.compliance", "nav.renamed.frameworks", "nav.renamed.controls"],
  "settings.log-shipping": ["nav.renamed.logShipping"],
  "settings.cloud": ["nav.renamed.cloud"],
  "settings.integrations": ["nav.renamed.integrations"],
  "settings.accounts": ["nav.renamed.accounts", "nav.renamed.people"],
  "settings.variables": ["nav.renamed.variables", "nav.renamed.fleetSettings"],
  "settings.general": ["nav.renamed.general"],
  "account.security": ["nav.renamed.security"],
};
