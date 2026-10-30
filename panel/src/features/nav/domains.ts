import { area, strip, tab } from "../../app/navCore";

/**
 * Domains: every certificate and every web server site on the machine, and the server's own
 * names. The register is app/nav.ts; this is one group of it. The ports a server publishes are the
 * server's (Network and ports, 4.1), and /domains/ports goes there. The names the server answers on
 * are its to manage, so a principal that cannot read the server is not offered those two tabs.
 */
export const DOMAINS_SCREENS = strip(
  area("domains", "/domains", "nav.domains.label", {
    scope: "both",
    group: "reach",
    fleetTo: "/fleet/certificates",
    shortcut: ["g", "n"],
    goTo: "nav.domains.goTo",
    keywords: "nav.domains.keywords",
    rootLabel: "domains.page.certificatesTab",
  }),
  (domains) => [
    tab(domains, "sites", "domains.page.sitesTab", { exact: true }),
    tab(domains, "dns", "network.tabs.dns", { exact: true, permission: "server.read" }),
    tab(domains, "tunnels", "network.tabs.tunnels", { exact: true, permission: "server.read" }),
  ],
);
