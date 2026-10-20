import { area, strip, tab } from "../../app/navCore";
import { ICONS } from "../../components/ui/icons";

/**
 * Domains: every certificate and every web server site on the machine, and the server's own
 * network. The register is app/nav.ts; this is one group of it. The network is the server's to
 * manage, so a principal that cannot read the server is not offered those three tabs.
 */
export const DOMAINS_SCREENS = strip(
  area("domains", "/domains", "nav.domains.label", ICONS.verified, {
    group: "runs",
    shortcut: ["g", "n"],
    goTo: "nav.domains.goTo",
    keywords: "nav.domains.keywords",
    rootLabel: "domains.page.certificatesTab",
  }),
  (domains) => [
    tab(domains, "sites", "domains.page.sitesTab", { exact: true }),
    tab(domains, "dns", "network.tabs.dns", { exact: true, permission: "server.read" }),
    tab(domains, "tunnels", "network.tabs.tunnels", { exact: true, permission: "server.read" }),
    tab(domains, "ports", "network.tabs.ports", { exact: true, permission: "server.read" }),
  ],
);
