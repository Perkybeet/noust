import { createFileRoute } from "@tanstack/react-router";

import { HomePage } from "../../../features/overview/HomePage";

/** Home across every server: the same page as a server's own, over the fleet. */
export const Route = createFileRoute("/_console/fleet/")({
  component: FleetHomeRoute,
});

function FleetHomeRoute() {
  return <HomePage scope="fleet" />;
}
