import { area, hidden, strip, tab } from "../../app/navCore";
import type { ConsolePath, Dest, More } from "../../app/navCore";
import type { PlainKey } from "../../i18n";

/**
 * A page of the Docker zone (item 189): its path is `/server/docker/<name>` and its id `server.docker.<name>`. Not
 * `tab(server, "docker/stacks")`, which would put a slash in an id; the entry is the same one in every other respect.
 */
const docker = (server: Dest, name: string, label: PlainKey, more: More): Dest => ({
  ...tab(server, `docker.${name}`, label, more),
  to: `/server/docker/${name}` as ConsolePath,
});

/**
 * The machine, which is Server on the bar (Servers over every server): its sections, and Terminal, which is
 * reached from "More actions" as well. Where the console holds a fleet the area opens the list of servers
 * (`fleetTo`), and each server has its page, this one. The register is app/nav.ts; this is one group of it.
 *
 * The sections are in the order an operator goes through a machine in, in the groups of the column: this
 * machine (how it is, the system, what runs on it, what it uses and what waits to be updated), what runs on
 * it (services and the web server in front of them), its tools (files and a shell) and what the central may
 * do there. The views of the System section of 4.1 are sections of their own now, and Security and Network
 * are areas of their own (features/nav/security.ts and network.ts). Databases of a server is Databases
 * filtered by it, not a section of its own.
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
    column: "machine",
    fleetLabel: "nav.fleetBar.servers.label",
  }),
  (server) => [
    tab(server, "machine", "server.system.views.machine", { column: "machine", feature: "system_clock", keywords: "server.tabs.systemKeywords" }),
    // What was General about the machine (item 166): where its applications and backups live, what it builds at once.
    tab(server, "folders", "server.system.views.folders", { column: "machine", keywords: "server.tabs.foldersKeywords", permission: "server.read" }),
    tab(server, "processes", "server.system.views.processes", { column: "machine", keywords: "nav.screens.processes.keywords" }),
    tab(server, "monitor", "server.system.views.monitor", { column: "machine", keywords: "nav.screens.monitor.keywords" }),
    tab(server, "updates", "server.system.views.updates", { column: "machine", fleetTo: "/fleet/updates", fleetLabel: "nav.fleetBar.updates.label", keywords: "nav.screens.updates.keywords" }),
    tab(server, "services", "server.tabs.services", { column: "running", feature: "services" }),
    tab(server, "webserver", "server.tabs.webserver", { column: "running", feature: "sites", keywords: "server.tabs.webserverKeywords", permission: "server.read" }),
    // Publishing this machine's console on a name of its own over HTTPS: the machine's, whatever the central carries to the others.
    tab(server, "console", "server.tabs.console", { column: "running", feature: "console_exposure", keywords: "server.tabs.consoleKeywords", permission: "settings.read" }),
    tab(server, "software", "server.tabs.software", { column: "running", feature: "software", fleetTo: "/fleet/software", fleetLabel: "software.fleet.tab", keywords: "server.tabs.softwareKeywords", permission: "server.read" }),
    // Docker, seen as the machine's own: what runs in it, whose each thing is and what it holds. The zone needs the function the
    // table calls `docker_zone`: a server that has no Docker does not offer it, and a typed address says why (`FeatureGate`).
    // With All servers each page is the fleet's own (`fleetTo`), one list of every server's with the server as a column.
    tab(server, "docker", "server.docker.summary", {
      column: "docker",
      exact: true,
      feature: "docker_zone",
      fleetTo: "/fleet/docker",
      permission: "docker.read",
      keywords: "server.docker.summaryKeywords",
      command: "server.docker.summaryCommand",
    }),
    docker(server, "stacks", "server.docker.stacks", { column: "docker", feature: "docker_zone", fleetTo: "/fleet/docker/stacks", permission: "docker.read", keywords: "server.docker.stacksKeywords", command: "server.docker.stacksCommand" }),
    docker(server, "containers", "server.docker.containers", {
      column: "docker",
      feature: "docker_zone",
      fleetTo: "/fleet/docker/containers",
      permission: "docker.read",
      keywords: "server.docker.containersKeywords",
      command: "server.docker.containersCommand",
    }),
    docker(server, "images", "server.docker.images", { column: "docker", feature: "docker_zone", fleetTo: "/fleet/docker/images", permission: "docker.read", keywords: "server.docker.imagesKeywords", command: "server.docker.imagesCommand" }),
    docker(server, "volumes", "server.docker.volumes", { column: "docker", feature: "docker_zone", fleetTo: "/fleet/docker/volumes", permission: "docker.read", keywords: "server.docker.volumesKeywords", command: "server.docker.volumesCommand" }),
    docker(server, "networks", "server.docker.networks", {
      column: "docker",
      feature: "docker_zone",
      fleetTo: "/fleet/docker/networks",
      permission: "docker.read",
      keywords: "server.docker.networksKeywords",
      command: "server.docker.networksCommand",
    }),
    docker(server, "engine", "server.docker.engine", { column: "docker", feature: "docker_zone", fleetTo: "/fleet/docker/engine", permission: "docker.read", keywords: "server.docker.engineKeywords", command: "server.docker.engineCommand" }),
    tab(server, "files", "server.tabs.files", { column: "tools", keywords: "server.tabs.filesKeywords" }),
    // The machine written down in a file: what applying it would change here, before anything does.
    tab(server, "manifest", "manifestPreview.nav.label", { column: "tools", keywords: "manifestPreview.nav.keywords", command: "manifestPreview.nav.command", permission: "apps.read", feature: "applications" }),
    hidden("server.terminal", "/server/terminal", "terminal.page.title", "server", {
      parent: "server",
      permission: "server.terminal",
      feature: "root_shell",
      intent: "fix",
      column: "tools",
      keywords: "nav.screens.terminal.keywords",
    }),
    tab(server, "central", "server.tabs.central", { column: "fleet", keywords: "server.tabs.centralKeywords", when: "fleet" }),
  ],
);

/** A service of the machine, under Server > Services. */
export const SERVICE_SCREENS = strip(hidden("service", "/server/services/$name", "services.tabs.overview", "server.services", { parent: "server.services", exact: true, feature: "services" }), (service) => [
  tab(service, "logs", "services.tabs.logs"),
]);
