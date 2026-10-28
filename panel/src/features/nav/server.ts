import { area, hidden, strip, tab } from "../../app/navCore";

/**
 * The machine, which is Servers on the bar: its tabs, and Terminal, which is reached from "More
 * actions". Where the console holds a fleet the area opens the list of servers (`fleetTo`), and
 * each server has its page, this one. The register is app/nav.ts; this is one group of it.
 */
export const SERVER_SCREENS = strip(
  area("server", "/server", "nav.server.label", {
    scope: "both",
    group: "where",
    fleetTo: "/fleet/servers",
    shortcut: ["g", "v"],
    goTo: "nav.server.goTo",
    keywords: "nav.server.keywords",
    rootLabel: "server.tabs.overview",
  }),
  (server) => [
    tab(server, "updates", "server.tabs.updates"),
    tab(server, "security", "server.tabs.security"),
    tab(server, "storage", "server.tabs.storage"),
    tab(server, "services", "server.tabs.services"),
    tab(server, "system", "server.tabs.system"),
    hidden("server.terminal", "/server/terminal", "terminal.page.title", "server", {
      parent: "server",
      permission: "server.terminal",
      intent: "fix",
      keywords: "nav.screens.terminal.keywords",
    }),
  ],
);

/** A service of the machine, under Server > Services. */
export const SERVICE_SCREENS = strip(hidden("service", "/server/services/$name", "services.tabs.overview", "server.services", { parent: "server.services", exact: true }), (service) => [
  tab(service, "logs", "services.tabs.logs"),
]);
