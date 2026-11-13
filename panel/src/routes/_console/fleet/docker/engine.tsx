import { createFileRoute } from "@tanstack/react-router";

import { FleetEngine } from "../../../../features/docker/fleet/FleetSummary";

/** Servers > Docker > Engine: which Docker each server has and what fits in it. */
export const Route = createFileRoute("/_console/fleet/docker/engine")({
  component: FleetEngine,
});
