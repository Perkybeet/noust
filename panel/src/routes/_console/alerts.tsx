import { createFileRoute } from "@tanstack/react-router";

import { AlertsLayout } from "../../features/alerts/AlertsLayout";

/** Alerts: the rules on the recorded metrics, and the probes and the status page, each a URL. */
export const Route = createFileRoute("/_console/alerts")({
  component: AlertsLayout,
});
