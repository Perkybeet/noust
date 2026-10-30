import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { FleetAlerts } from "../../../features/alerts/FleetAlerts";
import { validateFleetSearch } from "../../../features/fleet/filters";

/** Alerts on every server: the rules of each one, the ones firing first, each naming its server. */
export const Route = createFileRoute("/_console/fleet/alerts")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <FleetAlerts
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })}
    />
  );
}
