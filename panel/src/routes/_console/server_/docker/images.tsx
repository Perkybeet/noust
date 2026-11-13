import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { ImagesPage } from "../../../../features/docker/ImagesPage";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Every image Docker holds, whose it is and where it stands against its registry. */
export const Route = createFileRoute("/_console/server_/docker/images")({
  validateSearch: validateDockerSearch,
  component: ImagesRoute,
});

function ImagesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ImagesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
