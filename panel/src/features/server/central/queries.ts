import { queryOptions } from "@tanstack/react-query";

import type { ResponseOf } from "../../../api/client";
import { nodeGet } from "../../fleet/nodes";

export type FleetSelf = ResponseOf<"/api/auth/fleet/self", "get">;

/** The capability a Noust before 3.1 lacks: it publishes no ceiling, which a central shows as unknown. */
export const FLEET_SELF_CAPABILITY = "GET /api/auth/fleet/self";

export const centralKeys = {
  self: (node: string) => ["server", "central", "self", node] as const,
};

/**
 * The most this central may do on a server, as that server publishes it. `/api/auth` is always the
 * central's own in the console (api/nodeScope.ts), so this asks the server through the proxy by
 * name, the way the fleet does. Read again on demand: the ceiling is changed on the server's own
 * command line, and "Check again" is how the console learns it was.
 */
export const fleetSelfQuery = (node: string) =>
  queryOptions({
    queryKey: centralKeys.self(node),
    queryFn: ({ signal }) => nodeGet<FleetSelf>(node, "/api/auth/fleet/self", signal),
    staleTime: 0,
  });
