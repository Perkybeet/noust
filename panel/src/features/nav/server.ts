import { area, hidden, strip, tab } from "../../app/navCore";

/**
 * The machine, which is Servers on the bar: its sections (4.1, the page of each server), and Terminal,
 * which is reached from "More actions" on one bar. Where the console holds a fleet the area opens the
 * list of servers (`fleetTo`), and each server has its page, this one. The register is app/nav.ts;
 * this is one group of it.
 *
 * The order is the one an operator goes through a machine in: how it is, the system and what it
 * waits to be updated with, who may get in, what is open to the network, what runs on it, the web
 * server in front of it, a shell, where its files are, and what the central may do there. Bases de
 * datos del servidor is Databases filtered by this server, not a section of its own.
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
    tab(server, "system", "server.tabs.system", { keywords: "server.tabs.systemKeywords" }),
    tab(server, "security", "server.tabs.security", { keywords: "server.tabs.securityKeywords" }),
    tab(server, "network", "server.tabs.network", { keywords: "server.tabs.networkKeywords", permission: "server.read" }),
    tab(server, "services", "server.tabs.services"),
    tab(server, "webserver", "server.tabs.webserver", { keywords: "server.tabs.webserverKeywords", permission: "server.read" }),
    hidden("server.terminal", "/server/terminal", "terminal.page.title", "server", {
      parent: "server",
      permission: "server.terminal",
      intent: "fix",
      keywords: "nav.screens.terminal.keywords",
    }),
    tab(server, "files", "server.tabs.files", { keywords: "server.tabs.filesKeywords" }),
    tab(server, "central", "server.tabs.central", { keywords: "server.tabs.centralKeywords", when: "fleet" }),
  ],
);

/** A service of the machine, under Server > Services. */
export const SERVICE_SCREENS = strip(hidden("service", "/server/services/$name", "services.tabs.overview", "server.services", { parent: "server.services", exact: true }), (service) => [
  tab(service, "logs", "services.tabs.logs"),
]);
