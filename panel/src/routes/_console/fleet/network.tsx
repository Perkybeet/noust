import { createFileRoute } from "@tanstack/react-router";

import { MachineAreaServers } from "../../../features/fleet/MachineAreaServers";

/** Fleet > Network: the servers, to choose one: what this area holds belongs to a machine. */
export const Route = createFileRoute("/_console/fleet/network")({
  component: () => <MachineAreaServers area="network" />,
});
