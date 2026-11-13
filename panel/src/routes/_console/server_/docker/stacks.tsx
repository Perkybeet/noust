import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { StacksPage } from "../../../../features/docker/StacksPage";
import { validateDockerSearch } from "../../../../features/docker/search";

/** The Compose projects that run: the ones Noust looks after and the ones nobody of Noust made. */
export const Route = createFileRoute("/_console/server_/docker/stacks")({
  validateSearch: validateDockerSearch,
  component: StacksRoute,
});

function StacksRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <StacksPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
