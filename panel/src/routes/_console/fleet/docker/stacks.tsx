import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { FleetStacks } from "../../../../features/docker/fleet/FleetLists";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Servers > Docker > Stacks: the stacks of every server, the server as a column and a filter. */
export const Route = createFileRoute("/_console/fleet/docker/stacks")({
  validateSearch: validateDockerSearch,
  component: FleetStacksRoute,
});

function FleetStacksRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <FleetStacks search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
