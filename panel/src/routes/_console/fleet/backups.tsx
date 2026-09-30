import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { validateFleetSearch } from "../../../features/fleet/filters";
import { BackupsTab } from "../../../features/fleet/BackupsTab";

/** Fleet > Backups: every application's backups, the gaps first. */
export const Route = createFileRoute("/_console/fleet/backups")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <BackupsTab
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })}
    />
  );
}
