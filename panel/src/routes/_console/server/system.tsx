import { createFileRoute } from "@tanstack/react-router";

import { SystemTab } from "../../../features/server/system/SystemTab";
import { validateSystemSearch } from "../../../features/server/system/data";

/** The machine itself: clock, name, system, power, and `?view=processes|network|monitor` below. */
export const Route = createFileRoute("/_console/server/system")({
  validateSearch: validateSystemSearch,
  component: SystemRoute,
});

function SystemRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <SystemTab view={search.view ?? "processes"} onViewChange={(view) => void navigate({ search: view === "processes" ? {} : { view }, replace: true })} />;
}
