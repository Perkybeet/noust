import { createFileRoute } from "@tanstack/react-router";

import { ProbeSettings } from "../../../features/settings/probes/ProbeSettings";

/** Alerts > Probes: addresses asked from outside the application, and the public status page. */
export const Route = createFileRoute("/_console/alerts/probes")({
  component: ProbeSettings,
});
