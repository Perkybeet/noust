import { hidden, tab, tabsOf } from "../../app/navCore";
import type { Strip, TabItem } from "../../app/navCore";
import { app, appSettings } from "./appRoot";

// An application. Its page is the Overview tab of its own strip, and its settings are a list on
// the side of the last tab. The register is app/nav.ts; these are groups of it.


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
