import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { validateFleetSearch } from "../../../features/fleet/filters";
import { ServersTab } from "../../../features/fleet/ServersTab";

/** Fleet > Servers: every server, its state, version, labels and access. */
export const Route = createFileRoute("/_console/fleet/servers")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ServersTab
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })}
    />
  );
}
