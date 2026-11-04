import { createFileRoute } from "@tanstack/react-router";

import { SystemTab } from "../../../features/server/system/SystemTab";

/** What runs on the machine and what it costs (the `?view=processes` of System in 4.1). */
export const Route = createFileRoute("/_console/server/processes")({
  component: ProcessesRoute,
});

function ProcessesRoute() {
  return <SystemTab view="processes" />;
}
