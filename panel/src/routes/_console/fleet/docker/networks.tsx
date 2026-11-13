import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { FleetNetworks } from "../../../../features/docker/fleet/FleetLists";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Servers > Docker > Networks: the networks of every server, the server as a column and a filter. */
export const Route = createFileRoute("/_console/fleet/docker/networks")({
  validateSearch: validateDockerSearch,
  component: FleetNetworksRoute,
});

function FleetNetworksRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <FleetNetworks search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
