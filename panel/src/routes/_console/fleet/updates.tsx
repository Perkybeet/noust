import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { validateFleetSearch } from "../../../features/fleet/filters";
import { ServersTab } from "../../../features/fleet/ServersTab";

/**
 * Fleet > With an update: the servers' list, only those that are waiting for something (their Noust, their system, a
 * reboot). It was the second view of the servers' area in 4.1 with a table of its own; it is the same list now, so a
 * server's version and the press that updates it are in one place.
 */
export const Route = createFileRoute("/_console/fleet/updates")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ServersTab
      preset="updates"
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })}
    />
  );
}
