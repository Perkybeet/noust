import { createFileRoute } from "@tanstack/react-router";

import { PlacementsTab } from "../../../features/fleet/placements/PlacementsTab";

/** Fleet > On several servers: the applications that run on more than one server. */
export const Route = createFileRoute("/_console/fleet/placements")({
  component: PlacementsTab,
});
