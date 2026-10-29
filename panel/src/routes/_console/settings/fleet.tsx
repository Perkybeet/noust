import { createFileRoute } from "@tanstack/react-router";

import { FleetSettings } from "../../../features/settings/fleet/FleetSettings";

/** Settings > Fleet settings: what the fleet shares, which server follows it, and applying it. */
export const Route = createFileRoute("/_console/settings/fleet")({
  component: FleetSettings,
});
