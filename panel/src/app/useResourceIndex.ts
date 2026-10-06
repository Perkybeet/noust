import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { certsQuery } from "../api/queries/certs";
import { databasesQuery } from "../api/queries/databases";
import { servicesQuery } from "../api/queries/services";
import { sitesQuery } from "../api/queries/sites";
import { sessionQuery } from "../api/queries/auth";
import { recentDeploysQuery } from "../features/apps/data";
import type { ResourceIndex } from "./resourceCommands";

/**
 * How long a list read for the palette is good: opening and closing it repeatedly asks for
 * nothing. What changes in between is `applyServerEvent`'s to say (a finished job marks these
 * lists stale), not a timer's.
 */
const INDEX_STALE_MS = 60_000;

/**
 * The lists the command palette searches, read through the console's own queries (the lists the
 * pages read, so a page already open has already paid for them) and only once the palette has
 * been opened. A list the signed-in account may not read is never asked for, so a viewer without
 * database access is not answered with an error the palette would have to hide.
 *
 * Args:
 *     open: Whether the palette is open; nothing is read before the first time.
 *     enabled: False where these lists are not the server's own (the fleet's pages).
 *
 * Returns:
 *     What has arrived; each list is undefined until it does.
 */
export function useResourceIndex(open: boolean, enabled: boolean): ResourceIndex {
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  const may = (permission: string): boolean => open && enabled && (permissions?.includes(permission) ?? false);
  const options = { staleTime: INDEX_STALE_MS, retry: false } as const;

  // `apps.read` covers the sites, certificates and deployments; the services are the server's.
  const databases = useQuery({ ...databasesQuery(), ...options, enabled: may("databases.read") });
  const sites = useQuery({ ...sitesQuery(), ...options, enabled: may("apps.read") });
  const certificates = useQuery({ ...certsQuery(), ...options, enabled: may("apps.read") });
  const deployments = useQuery({ ...recentDeploysQuery(), ...options, enabled: may("apps.read") });
  const services = useQuery({ ...servicesQuery(), ...options, enabled: may("server.read") });

  // One object while nothing arrived: the palette's commands are rebuilt when this changes.
  return useMemo(
    () => ({
      databases: databases.data?.databases,
      sites: sites.data?.sites,
      certificates: certificates.data?.certificates,
      services: services.data?.services,
      deployments: deployments.data?.items,
    }),
    [databases.data, sites.data, certificates.data, services.data, deployments.data],
  );
}
