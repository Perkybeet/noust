import { createFileRoute, redirect } from "@tanstack/react-router";

import { validateNetworkSearch } from "../../../features/server/network/data";

/**
 * Network and ports moved out of the Server in 4.2: each of its views is a section of Network. The old address, with the
 * view it asked for and the port it looked up, goes to it.
 */
export const Route = createFileRoute("/_console/server/network")({
  validateSearch: validateNetworkSearch,
  beforeLoad: ({ search }) => {
    const to = search.view === "firewall" ? "/network/firewall" : search.view === "interfaces" ? "/network/interfaces" : "/network/listening";
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to, search: search.q !== undefined ? { q: search.q } : {}, replace: true });
  },
});
