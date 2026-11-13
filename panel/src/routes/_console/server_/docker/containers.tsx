import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { ContainersPage } from "../../../../features/docker/ContainersPage";
import { validateDockerSearch } from "../../../../features/docker/search";

/** Every container Docker has, with whose it is: `/server/docker/containers?app=shop.example.com&state=problem`. */
export const Route = createFileRoute("/_console/server_/docker/containers")({
  validateSearch: validateDockerSearch,
  component: ContainersRoute,
});

function ContainersRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ContainersPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
