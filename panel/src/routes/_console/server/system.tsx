import { createFileRoute, redirect } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { SystemTab } from "../../../features/server/system/SystemTab";
import { validateSystemSearch } from "../../../features/server/system/data";

/** System and updates: what is waiting to be updated, the machine, what runs on it and what it uses, as `?view=`. */
export const Route = createFileRoute("/_console/server/system")({
  validateSearch: validateSystemSearch,
  beforeLoad: ({ search }) => {
    // The interfaces were a view of the System tab in 4.0; they are Network and ports' now.
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (search.view === "network") throw redirect({ to: "/server/network", replace: true });
  },
  component: SystemRoute,
});

function SystemRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const view = search.view === undefined || search.view === "network" ? "updates" : search.view;
  return <SystemTab view={view} onViewChange={(next) => void navigate({ search: next === "updates" ? {} : { view: next }, ...inPlace({ replace: true }) })} />;
}
