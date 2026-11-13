import { createFileRoute } from "@tanstack/react-router";

import { FleetSummary } from "../../../../features/docker/fleet/FleetSummary";

/** Servers > Docker: what asks for the operator on any server, worst first, and each server's engine and counters. */
export const Route = createFileRoute("/_console/fleet/docker/")({
  component: FleetSummary,
});
