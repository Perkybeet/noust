import { createFileRoute } from "@tanstack/react-router";

import { NetworkServers } from "../../../features/network/NetworkServers";

/** Fleet > Network: the servers with what their network is, to choose one: what this area holds belongs to a machine. */
export const Route = createFileRoute("/_console/fleet/network")({
  component: NetworkServers,
});
