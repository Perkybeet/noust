import { createFileRoute } from "@tanstack/react-router";

import { AlertSettings } from "../../../features/settings/alerts/AlertSettings";

/** Settings > Alerts: rules on the recorded metrics, which of them are firing, and the built-in checks. */
export const Route = createFileRoute("/_console/settings/alerts")({
  component: AlertSettings,
});
