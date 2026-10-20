import { hidden, section, tab, tabsOf } from "../../app/navCore";
import type { Dest, Strip, TabItem } from "../../app/navCore";

// An application. Its page is the Overview tab of its own strip, and its settings are a list on
// the side of the last tab. The register is app/nav.ts; these are groups of it.

const app = hidden("app", "/apps/$domain", "nav.appTabs.overview.label", "apps", { parent: "apps", exact: true });
const appSettings = tab(app, "settings", "nav.appTabs.settings.label", { keywords: "nav.appTabs.settings.keywords", rootLabel: "appSettings.nav.general" });

export const APP_SCREENS: Strip = [
  app,
  tab(app, "deployments", "nav.appTabs.deployments.label", { keywords: "nav.appTabs.deployments.keywords" }),
  tab(app, "logs", "nav.appTabs.logs.label", { keywords: "nav.appTabs.logs.keywords" }),
  tab(app, "metrics", "nav.appTabs.metrics.label", { keywords: "nav.appTabs.metrics.keywords" }),
  tab(app, "environment", "nav.appTabs.environment.label", { keywords: "nav.appTabs.environment.keywords" }),
  tab(app, "database", "databases.appTab.tabLabel", { keywords: "databases.appTab.tabKeywords" }),
  tab(app, "domains", "nav.appTabs.domains.label", { keywords: "nav.appTabs.domains.keywords" }),
  appSettings,
  // Off the strip: reached from what is wrong (Diagnose) or from "More actions" (Terminal, Files).
  hidden("app.diagnose", "/apps/$domain/diagnose", "appPages.actions.diagnose", "app", { parent: "app", keywords: "nav.screens.diagnose.keywords" }),
  hidden("app.terminal", "/apps/$domain/terminal", "appPages.actions.terminal", "app", {
    parent: "app",
    permission: "apps.terminal",
    keywords: "nav.screens.terminal.keywords",
  }),
  hidden("app.files", "/apps/$domain/files", "runtime.files.title", "app", { parent: "app", permission: "apps.terminal", keywords: "nav.screens.files.keywords" }),
];

/** An application's sections (D8), eight at most (docs/DESIGN.md, "LinkTabs"): each a URL under /apps/$domain. */
export const APP_TABS: readonly TabItem[] = tabsOf(APP_SCREENS);

/**
 * The subsections of an application's settings (features/app/settings/sections.ts decides, per
 * application, which of them apply). The groups are the ones of 2.5 of the navigation design.
 */
const setting = (name: string, label: Dest["label"], group: string, more: Parameters<typeof section>[3] = {}): Dest =>
  section(appSettings, name, label, { scope: "server", owner: "resource", group, ...more });
export const APP_SETTINGS_SCREENS: Strip = [
  setting("source", "appSettings.nav.source", "code"),
  setting("build", "appSettings.nav.build", "code", { keywords: "appSettings.keywords.build" }),
  setting("container", "appSettings.nav.container", "code"),
  setting("deploys", "appSettings.nav.deploys", "deploys", { keywords: "appSettings.keywords.deploys" }),
  setting("automation", "appSettings.nav.automation", "deploys", { keywords: "appSettings.keywords.automation" }),
  setting("compose", "appSettings.nav.compose", "deploys", { when: "compose", keywords: "appSettings.keywords.compose" }),
  setting("deploy-on-push", "appSettings.nav.push", "triggers"),
  setting("hooks", "appSettings.nav.hooks", "triggers"),
  setting("previews", "appSettings.nav.previews", "triggers"),
  setting("resources", "appSettings.nav.resources", "runtime"),
  setting("disk", "runtime.nav.disk", "runtime"),
  setting("processes", "runtime.nav.processes", "runtime"),
  setting("mounts", "runtime.nav.mounts", "runtime"),
  setting("security", "runtime.nav.security", "runtime"),
  setting("access", "network.access.nav", "access"),
  setting("protection", "network.protection.nav", "access", { keywords: "appSettings.keywords.protection" }),
  setting("export", "appSettings.nav.export", "danger"),
  setting("delete", "appSettings.nav.delete", "danger"),
];
