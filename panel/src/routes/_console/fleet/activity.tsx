import { createFileRoute } from "@tanstack/react-router";

import { validateFleetSearch } from "../../../features/fleet/filters";
import { ActivityTab } from "../../../features/fleet/ActivityTab";

/** Fleet > Activity: what happened lately on every server. */
export const Route = createFileRoute("/_console/fleet/activity")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ActivityTab
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })}
    />
  );
}
