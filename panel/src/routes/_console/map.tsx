import { createFileRoute } from "@tanstack/react-router";

import { MapPage } from "../../features/map/MapPage";

/** How this server fits together, read from it: `/map`. */
export const Route = createFileRoute("/_console/map")({
  component: MapPage,
});
