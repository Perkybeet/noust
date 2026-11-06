import { createFileRoute } from "@tanstack/react-router";

import { MachineProviders } from "../../features/server/MachinePage";

/** Network: what the machine publishes and what it listens on, each a section of its column. */
export const Route = createFileRoute("/_console/network")({
  component: MachineProviders,
});
