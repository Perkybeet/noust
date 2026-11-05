import { createFileRoute } from "@tanstack/react-router";

import { FleetSoftware } from "../../../features/server/software/FleetSoftware";

/** Servers > Software: what is installed on each server of the fleet. */
export const Route = createFileRoute("/_console/fleet/software")({
  component: FleetSoftware,
});
