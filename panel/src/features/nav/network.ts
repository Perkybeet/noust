import { area, strip, tab } from "../../app/navCore";

/**
 * Network: what the machine publishes and what it listens on (4.2). The ports nginx publishes for what is not
 * a web page and the Cloudflare tunnels were in Domains, and the listening ports, the firewall and the interfaces
 * were views of the Server's Network and ports; a person who opens a port or makes a tunnel is not looking for a
 * domain. Domains keeps the names, their sites, certificates and DNS.
 *
 * It is the machine's network, so a principal that cannot read the server is not offered it, and across the
 * servers it is the list of them: choosing one is choosing it in the selector (`fleetTo`). The register is
 * app/nav.ts; this is one group of it.
 */
export const NETWORK_SCREENS = strip(
  area("network", "/network", "nav.network.label", {
    scope: "both",
    group: "reach",
    fleetTo: "/fleet/network",
    shortcut: ["g", "w"],
    goTo: "nav.network.goTo",
    keywords: "nav.network.keywords",
    permission: "server.read",
    feature: "published_ports",
    rootLabel: "domains.sections.ports.tab",
    column: "publish",
  }),
  (network) => [
    tab(network, "tunnels", "domains.sections.tunnels.tab", { exact: true, feature: "tunnels", permission: "server.read", column: "publish", keywords: "domains.sections.tunnels.keywords" }),
    tab(network, "firewall", "serverPorts.views.firewall", { exact: true, feature: "firewall", permission: "server.read", column: "machine", keywords: "nav.screens.firewall.keywords" }),
    tab(network, "listening", "serverPorts.views.ports", { exact: true, permission: "server.read", column: "machine", keywords: "server.tabs.networkKeywords" }),
    tab(network, "interfaces", "serverPorts.views.interfaces", { exact: true, permission: "server.read", column: "machine", keywords: "nav.screens.interfaces.keywords" }),
  ],
);
