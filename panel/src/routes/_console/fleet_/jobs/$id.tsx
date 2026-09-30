import { createFileRoute } from "@tanstack/react-router";

import { FleetJobPage } from "../../../../features/fleet/FleetJobPage";

/** One bulk action of the fleet, server by server, as it runs. */
export const Route = createFileRoute("/_console/fleet_/jobs/$id")({
  component: FleetJobRoute,
});

function FleetJobRoute() {
  const { id } = Route.useParams();
  return <FleetJobPage id={id} />;
}
