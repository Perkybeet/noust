import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { validateAppsSearch } from "../../../features/apps/filters";
import { FleetAppsPage } from "../../../features/fleet/FleetAppsPage";

/** Applications across the fleet: the same list as a server's, over every server. Filters are search params: `/fleet/apps?server=web-2&state=failed`. */
export const Route = createFileRoute("/_console/fleet/apps")({
  validateSearch: validateAppsSearch,
  component: FleetAppsRoute,
});

function FleetAppsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <FleetAppsPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
