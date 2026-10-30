import { area, hidden, strip, tab } from "../../app/navCore";

/**
 * Domains: the names the server answers on, the web server's site for each, their certificates, and
 * how the names are reached and answered: tunnels, DNS (a provider's or Noust's own) and the TCP and
 * UDP ports nginx carries. The register is app/nav.ts; this is one group of it. DNS, tunnels and ports
 * are the server's own network, so a principal that cannot read the server is not offered them.
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
  (domains) => [
    tab(domains, "sites", "domains.sections.sites.tab", { exact: true, keywords: "domains.sections.sites.keywords" }),
    tab(domains, "certificates", "domains.sections.certificates.tab", { exact: true, keywords: "domains.sections.certificates.keywords" }),
    // Not exact: Noust DNS and each of its zones are beneath it, and the tab is where they are.
    tab(domains, "dns", "domains.sections.dns.tab", { permission: "server.read", keywords: "domains.sections.dns.keywords" }),
    // Reached from the DNS tab, which offers it beside the providers; listed so the palette finds it by name.
    // Its zones are the page of one zone (an exception of app/nav.ts).
    hidden("domains.dns.noust", "/domains/dns/noust", "noustDns.title", "domains.dns", {
      parent: "domains.dns",
      permission: "server.read",
      keywords: "noustDns.keywords",
      command: "noustDns.command",
    }),
    tab(domains, "tunnels", "domains.sections.tunnels.tab", { exact: true, permission: "server.read", keywords: "domains.sections.tunnels.keywords" }),
    tab(domains, "ports", "domains.sections.ports.tab", { exact: true, permission: "server.read", keywords: "domains.sections.ports.keywords" }),
  ],
);
