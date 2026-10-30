import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { ActivityPage } from "../../../features/activity/ActivityPage";
import { validateActivitySearch } from "../../../features/activity/search";

/** Activity on every server: the same page as the server's, over what each server's audit log lets the operator read. */
export const Route = createFileRoute("/_console/fleet/activity")({
  validateSearch: validateActivitySearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ActivityPage scope="fleet" search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
