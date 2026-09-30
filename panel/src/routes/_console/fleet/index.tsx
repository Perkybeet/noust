import { createFileRoute } from "@tanstack/react-router";

import { SummaryTab } from "../../../features/fleet/SummaryTab";

/** Fleet > Summary: the fleet at a glance. */
export const Route = createFileRoute("/_console/fleet/")({
  component: SummaryTab,
});
