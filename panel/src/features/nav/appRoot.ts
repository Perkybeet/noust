import { hidden, tab } from "../../app/navCore";

// An application's own page and the tab its settings are under: the root of both its strip
// (features/nav/app.ts) and the list on the side of its settings (features/nav/appSettings.ts),
// which are two files so that the page of the settings does not read the texts of the strip.

/** An application's page, the Overview tab of its own strip. */
export const app = hidden("app", "/apps/$domain", "nav.appTabs.overview.label", "apps", { parent: "apps", exact: true });

/** The last tab of the strip, whose sections are the list on the side. */
export const appSettings = tab(app, "settings", "nav.appTabs.settings.label", { keywords: "nav.appTabs.settings.keywords", rootLabel: "appSettings.nav.general" });
