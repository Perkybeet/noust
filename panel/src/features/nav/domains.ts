import { area, strip, tab } from "../../app/navCore";

/**
 * Domains: the names the server answers on, the web server's site for each, their certificates, and
 * how the names are answered: DNS, a provider's or Noust's own. The tunnels and the TCP and UDP ports nginx
 * carries are the machine's network and are Network's since 4.2 (features/nav/network.ts). The register is
 * app/nav.ts; this is one group of it. DNS is the server's own, so a principal that cannot read the server is
 * not offered it.
 */
export const DOMAINS_SCREENS = strip(
  area("domains", "/domains", "nav.domains.label", {
    scope: "both",
    group: "reach",
    fleetTo: "/fleet/certificates",
    shortcut: ["g", "n"],
    goTo: "nav.domains.goTo",
    keywords: "nav.domains.keywords",
    rootLabel: "domains.sections.domains.tab",
  }),
  (domains) => {
    // Not exact: each zone of Noust DNS is beneath it (an exception of app/nav.ts), and the tab is where they are.
    const dns = tab(domains, "dns", "domains.sections.dns.tab", { permission: "server.read", column: "dns", keywords: "domains.sections.dns.keywords" });
    return [
      tab(domains, "sites", "domains.sections.sites.tab", { exact: true, keywords: "domains.sections.sites.keywords" }),
      tab(domains, "certificates", "domains.sections.certificates.tab", { exact: true, keywords: "domains.sections.certificates.keywords" }),
      dns,
      // Beside the providers in the column, and found by name in the palette. Its zones are the page of one zone.
      tab(dns, "noust", "noustDns.title", { permission: "server.read", column: "dns", keywords: "noustDns.keywords", command: "noustDns.command" }),
    ];
  },
);
