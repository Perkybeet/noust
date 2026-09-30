import { createFileRoute } from "@tanstack/react-router";

import { OverviewTab } from "../../../features/server/overview/OverviewTab";

export const Route = createFileRoute("/_console/server/")({
  component: OverviewTab,
});
