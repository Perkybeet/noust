import { createFileRoute } from "@tanstack/react-router";

import { ChecksPage } from "../../../features/alerts/ChecksPage";

/** Alerts > Checks: how far the checks the monitor makes without a rule go before they say so. */
export const Route = createFileRoute("/_console/alerts/checks")({
  component: ChecksPage,
});
