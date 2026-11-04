import { createFileRoute, redirect } from "@tanstack/react-router";

import { validateSecuritySearch } from "../../../features/server/security/data";

/**
 * Security and SSH moved out of the Server in 4.2: its views are the sections of Security. The firewall, which was a view of
 * this tab in 4.0, is Network's.
 */
export const Route = createFileRoute("/_console/server/security")({
  validateSearch: validateSecuritySearch,
  beforeLoad: ({ search }) => {
    const to = search.view === undefined ? "/security" : ({ ssh: "/security/ssh", bans: "/security/blocks", firewall: "/network/firewall" } as const)[search.view];
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to, replace: true });
  },
});
