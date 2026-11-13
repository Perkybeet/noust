import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { FleetVolumes } from "../../../../features/docker/fleet/FleetLists";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Servers > Docker > Volumes: the volumes of every server, the server as a column and a filter. */
export const Route = createFileRoute("/_console/fleet/docker/volumes")({
  validateSearch: validateDockerSearch,
  component: FleetVolumesRoute,
});

function FleetVolumesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <FleetVolumes search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
