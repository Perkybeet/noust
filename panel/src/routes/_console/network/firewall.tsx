import { createFileRoute } from "@tanstack/react-router";

import { FirewallPage } from "../../../features/network/FirewallPage";

/** Network > firewall: the server's own, with its rules and who made each (it was a `?view=` of the Server's Network and ports in 4.1). */
export const Route = createFileRoute("/_console/network/firewall")({
  component: FirewallPage,
});
