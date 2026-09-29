import { createFileRoute } from "@tanstack/react-router";

import { parseHubArea } from "../../features/central/central";
import type { HubArea } from "../../features/central/central";
import { FleetPage } from "../../features/fleet/FleetPage";

interface FleetSearch {
  /** A hub was asked for one of the pages it does not have, and opened the fleet instead. */
  hub?: HubArea | "overview";
}

function validateSearch(search: Record<string, unknown>): FleetSearch {
  const hub = parseHubArea(search["hub"]);
  return hub === undefined ? {} : { hub };
}

/** The fleet: this server and every server this central manages. */
export const Route = createFileRoute("/_console/fleet")({
  validateSearch,
  component: FleetRoute,
});

function FleetRoute() {
  const { hub } = Route.useSearch();
  return <FleetPage hub={hub} />;
}
