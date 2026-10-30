import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { FleetBackups } from "../../../features/backups/FleetBackups";
import { validateFleetSearch } from "../../../features/fleet/filters";

/** Backups on every server: the page of Backups over each server's applications, the gaps first. */
export const Route = createFileRoute("/_console/fleet/backups")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <FleetBackups
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })}
    />
  );
}
