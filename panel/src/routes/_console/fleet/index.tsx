import { createFileRoute } from "@tanstack/react-router";

import { HomePage } from "../../../features/overview/HomePage";
import { validateHomeSearch } from "../../../features/overview/search";

/** Home across every server: the same page as a server's own, over the fleet. `?panel=` names one of the person's own. */
export const Route = createFileRoute("/_console/fleet/")({
  validateSearch: validateHomeSearch,
  component: FleetHomeRoute,
});

function FleetHomeRoute() {
  const { panel, customise } = Route.useSearch();
  return <HomePage scope="fleet" panelId={panel} customising={customise === true} />;
}
