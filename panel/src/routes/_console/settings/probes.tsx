import { createFileRoute } from "@tanstack/react-router";

import { ProbeSettings } from "../../../features/settings/probes/ProbeSettings";

/** Settings > Probes: addresses asked from outside the application, and the public status page. */
export const Route = createFileRoute("/_console/settings/probes")({
  component: ProbeSettings,
});
