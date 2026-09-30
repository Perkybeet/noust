import { createFileRoute } from "@tanstack/react-router";

import { parseHubArea } from "../../features/central/central";
import type { HubArea } from "../../features/central/central";
import { FleetLayout } from "../../features/fleet/FleetLayout";

interface FleetLayoutSearch {
  /** A hub was asked for one of the pages it does not have, and opened the fleet instead. */
  hub?: HubArea | "overview";
}

function validateSearch(search: Record<string, unknown>): FleetLayoutSearch {
  const hub = parseHubArea(search["hub"]);
  return hub === undefined ? {} : { hub };
}

/** The fleet ("All servers"): its header and one view per URL, every server at once. */
export const Route = createFileRoute("/_console/fleet")({
  validateSearch,
  component: FleetRoute,
});

function FleetRoute() {
  const { hub } = Route.useSearch();
  return <FleetLayout hub={hub} />;
}
