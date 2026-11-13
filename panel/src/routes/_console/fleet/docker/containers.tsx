import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { FleetContainers } from "../../../../features/docker/fleet/FleetLists";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Servers > Docker > Containers: the containers of every server, the server as a column and a filter. */
export const Route = createFileRoute("/_console/fleet/docker/containers")({
  validateSearch: validateDockerSearch,
  component: FleetContainersRoute,
});

function FleetContainersRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <FleetContainers search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
