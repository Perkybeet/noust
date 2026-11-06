import { createFileRoute } from "@tanstack/react-router";

import { FleetSecurity } from "../../../features/server/security/FleetSecurity";

/** Fleet > Security: the servers, each with its own security at a glance, to choose one: what this area holds belongs to a machine. */
export const Route = createFileRoute("/_console/fleet/security")({
  component: FleetSecurity,
});
