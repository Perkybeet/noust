import { createFileRoute } from "@tanstack/react-router";

import { MachineAreaServers } from "../../../features/fleet/MachineAreaServers";

/** Fleet > Security: the servers, to choose one: what this area holds belongs to a machine. */
export const Route = createFileRoute("/_console/fleet/security")({
  component: () => <MachineAreaServers area="security" />,
});
