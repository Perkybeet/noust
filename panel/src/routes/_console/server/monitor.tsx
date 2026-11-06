import { createFileRoute } from "@tanstack/react-router";

import { SystemTab } from "../../../features/server/system/SystemTab";

/** What the machine uses, over time (the `?view=monitor` of System in 4.1). */
export const Route = createFileRoute("/_console/server/monitor")({
  component: MonitorRoute,
});

function MonitorRoute() {
  return <SystemTab view="monitor" />;
}
