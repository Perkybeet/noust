import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { NetworksPage } from "../../../../features/docker/NetworksPage";
import { validateDockerSearch } from "../../../../features/docker/search";

/** The networks Docker has, whose each one is and who is on it. */
export const Route = createFileRoute("/_console/server_/docker/networks")({
  validateSearch: validateDockerSearch,
  component: NetworksRoute,
});

function NetworksRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <NetworksPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
