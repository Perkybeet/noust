import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { VolumesPage } from "../../../../features/docker/VolumesPage";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Where the data of every container lives: whose each volume is, how much it holds and who uses it. */
export const Route = createFileRoute("/_console/server_/docker/volumes")({
  validateSearch: validateDockerSearch,
  component: VolumesRoute,
});

function VolumesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <VolumesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
