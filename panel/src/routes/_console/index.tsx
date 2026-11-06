import { createFileRoute } from "@tanstack/react-router";

import { HomePage } from "../../features/overview/HomePage";
import { validateHomeSearch } from "../../features/overview/search";

/**
 * Home: a panel of widgets for the server chosen. `?panel=` names one of the person's own, and `?customise` is the mode
 * in which the column beside the page is the gallery of widgets.
 */
export const Route = createFileRoute("/_console/")({
  validateSearch: validateHomeSearch,
  component: HomeRoute,
});

function HomeRoute() {
  const { panel, customise } = Route.useSearch();
  return <HomePage scope="server" panelId={panel} customising={customise === true} />;
}
