import { area, strip, tab } from "../../app/navCore";

/**
 * Security: the machine's hardening checks, who may sign in over SSH, who is blocked for guessing, the web firewall
 * and who has entered (4.2). The first three were views of the Server's Security and SSH, buried under a section of
 * another area; keeping a machine safe is one of the things the bar says. What is of the instance (accounts,
 * compliance, audit) stays in Settings: Settings is how Noust is managed and this is the machine.
 *
 * Across the servers it is the list of them (`fleetTo`). The register is app/nav.ts; this is one group of it.
 */
export const SECURITY_SCREENS = strip(
  area("security", "/security", "nav.security.label", {
    scope: "both",
    group: "safe",
    fleetTo: "/fleet/security",
    shortcut: ["g", "i"],
    goTo: "nav.security.goTo",
    keywords: "nav.security.keywords",
    permission: "server.read",
    feature: "server_security",
    rootLabel: "server.security.views.checks",
  }),
  (security) => [
    tab(security, "ssh", "server.security.views.ssh", { exact: true, permission: "server.read", feature: "server_security", keywords: "nav.screens.ssh.keywords" }),
    tab(security, "blocks", "server.security.views.bans", { exact: true, permission: "server.read", feature: "server_security", keywords: "nav.screens.blocks.keywords" }),
    tab(security, "waf", "server.security.views.waf", { exact: true, permission: "server.read", feature: "server_security", keywords: "nav.screens.waf.keywords" }),
    tab(security, "access", "server.security.views.access", { exact: true, permission: "server.read", feature: "server_security", keywords: "nav.screens.access.keywords" }),
  ],
);
