import { createFileRoute } from "@tanstack/react-router";

import { validateFleetSearch } from "../../../features/fleet/filters";
import { UpdatesTab } from "../../../features/fleet/UpdatesTab";

/** Fleet > Updates: every server's Noust and system updates. */
export const Route = createFileRoute("/_console/fleet/updates")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <UpdatesTab
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })}
    />
  );
}
