import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { FleetImages } from "../../../../features/docker/fleet/FleetLists";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Servers > Docker > Images: the images of every server, the server as a column and a filter. */
export const Route = createFileRoute("/_console/fleet/docker/images")({
  validateSearch: validateDockerSearch,
  component: FleetImagesRoute,
});

function FleetImagesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <FleetImages search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
