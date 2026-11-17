import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { enginesQuery, serversQuery } from "../../../api/queries/databases";
import type { DbServer, ServerList } from "../../../api/queries/databases";
import { useNodeCapability } from "../../../nodes/capability";
import { serversFromEngines } from "../servers";

/** What a server is asked for to list its database servers: its own route, and the console's when the node has none. */
export const SERVERS_CAPABILITY = "GET /api/databases/servers";

/** What the table of functions says of the system's own engines: whether this machine has any, and the server's sentence for why not. */
export interface SystemEngines {
  available: boolean;
  /** The sentence for why the machine has none (`features.containers_only` on a machine that only has Docker); null when it has them. */
  reason: NonNullable<ServerList["system"]["reason"]> | null;
}

/** A server that says nothing of it (a node of 4.2, whose lists are the system's and the instances together) has the system's engines. */
const HAS_SYSTEM: SystemEngines = { available: true, reason: null };

export interface DatabaseServers {
  /** The servers, `undefined` while they are being read. */
  servers: readonly DbServer[] | undefined;
  /** What could not be asked (Docker not answering, an engine that refused Noust), so a short list is never taken for a complete one. */
  problems: NonNullable<ServerList["problems"]>;
  /** Whether the machine has database engines of its own: not offered an install of one where it has none (item 188). */
  system: SystemEngines;
  /** The node has no list of servers (it is older than 4.1) and these are drawn from its engines. */
  older: boolean;
  isError: boolean;
  error: unknown;
  isRefetching: boolean;
  refetch: () => void;
}

/**
 * The database servers of the server on screen: its own list when it has one, and its engines drawn as
 * servers when it does not (a node of 4.0, which has engines, instances and no default). The answer is
 * decided by what the node declares it offers (`useNodeCapability`), never by a request that fails.
 */
export function useDatabaseServers(): DatabaseServers {
  const capability = useNodeCapability(SERVERS_CAPABILITY);
  const older = capability.status === "missing";
  const listed = useQuery({ ...serversQuery(), enabled: capability.status === "available" || capability.status === "unknown" });
  const engines = useQuery({ ...enginesQuery(), enabled: older });
  const fromEngines = useMemo(() => (engines.data === undefined ? undefined : serversFromEngines(engines.data.engines)), [engines.data]);
  const source = older ? engines : listed;
  // Absent from a node of 4.2, which the types do not say: the lists of a node before this one carry no `system`.
  const said = older ? engines.data?.system : listed.data?.system;
  return {
    servers: older ? fromEngines : listed.data?.servers,
    problems: older ? [] : (listed.data?.problems ?? []),
    system: said === undefined ? HAS_SYSTEM : { available: said.available, reason: said.reason ?? null },
    older,
    isError: source.isError,
    error: source.error,
    isRefetching: source.isRefetching,
    refetch: () => void source.refetch(),
  };
}
