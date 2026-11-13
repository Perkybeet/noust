import { hidden, tab, tabsOf } from "../../app/navCore";
import type { Strip, TabItem } from "../../app/navCore";

// An application. Its page is the Overview of its own column, and the column lists every section of it,
// seventeen in five groups (4.2, item 116): there is no list of its own inside a "Settings". The register is
// app/nav.ts; these are groups of it.

// Each section says what it is for (`intent`), which is how the second column groups them: use it,
// configure it, fix it, take it somewhere else, or what is dangerous.
const app = hidden("app", "/apps/$domain", "nav.appTabs.overview.label", "apps", { parent: "apps", exact: true, intent: "use", feature: "applications" });

export const APP_SCREENS: Strip = [
  app,
  tab(app, "deployments", "nav.appTabs.deployments.label", { keywords: "nav.appTabs.deployments.keywords", intent: "use" }),
  tab(app, "logs", "nav.appTabs.logs.label", { keywords: "nav.appTabs.logs.keywords", intent: "use" }),
  tab(app, "metrics", "nav.appTabs.metrics.label", { keywords: "nav.appTabs.metrics.keywords", intent: "use" }),
  tab(app, "environment", "nav.appTabs.environment.label", { keywords: "nav.appTabs.environment.keywords", intent: "configure" }),
  tab(app, "domains", "nav.appTabs.domains.label", { keywords: "nav.appTabs.domains.keywords", intent: "configure" }),
  tab(app, "database", "databases.appTab.tabLabel", { keywords: "databases.appTab.tabKeywords", intent: "configure" }),
  // What used to be eighteen subsections of "Settings" is four pages, each of titled surfaces: the old names of the
  // subsections are the pages' search words, so the palette still finds "Previews" or "Mounts" by what it was called.
  tab(app, "code", "nav.appTabs.code.label", { keywords: "nav.appTabs.code.keywords", intent: "configure" }),
  tab(app, "triggers", "nav.appTabs.triggers.label", { keywords: "nav.appTabs.triggers.keywords", intent: "configure" }),
  tab(app, "resources", "nav.appTabs.resources.label", { keywords: "nav.appTabs.resources.keywords", intent: "configure" }),
  tab(app, "access", "nav.appTabs.access.label", { keywords: "nav.appTabs.access.keywords", intent: "configure" }),
  // Off the strip: reached from what is wrong (Diagnose) or from "More actions" (Terminal, Files).
  hidden("app.diagnose", "/apps/$domain/diagnose", "appPages.actions.diagnose", "app", { parent: "app", intent: "fix", feature: "applications", keywords: "nav.screens.diagnose.keywords" }),
  hidden("app.terminal", "/apps/$domain/terminal", "appPages.actions.terminal", "app", {
    parent: "app",
    intent: "fix",
    feature: "applications",
    permission: "apps.terminal",
    keywords: "nav.screens.terminal.keywords",
  }),
  hidden("app.files", "/apps/$domain/files", "runtime.files.title", "app", { parent: "app", intent: "fix", feature: "applications", permission: "apps.terminal", keywords: "nav.screens.files.keywords" }),
  // Taking an application to another server is not dangerous (3.4): it has its own group of the column, "Move elsewhere".
  tab(app, "export", "nav.appTabs.export.label", { keywords: "nav.appTabs.export.keywords", intent: "move" }),
  tab(app, "servers", "nav.appTabs.servers.label", { keywords: "nav.appTabs.servers.keywords", intent: "move" }),
  tab(app, "delete", "common.words.delete", { keywords: "nav.appTabs.delete.keywords", intent: "danger" }),
];

/** An application's sections (D8): each a URL under /apps/$domain, listed by the second column and never as a strip. */
export const APP_TABS: readonly TabItem[] = tabsOf(APP_SCREENS);
