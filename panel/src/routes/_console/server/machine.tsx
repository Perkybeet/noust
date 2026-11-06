import { createFileRoute } from "@tanstack/react-router";

import { SystemTab } from "../../../features/server/system/SystemTab";

/** The machine: its clock, its name, what it runs on and the power (the `?view=machine` of System in 4.1). */
export const Route = createFileRoute("/_console/server/machine")({
  component: MachineRoute,
});

function MachineRoute() {
  return <SystemTab view="machine" />;
}
