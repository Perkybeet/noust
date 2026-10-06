import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { appsQuery } from "../../api/queries/apps";
import type { AppList } from "../../api/queries/apps";
import { databasesQuery } from "../../api/queries/databases";
import type { DatabaseList } from "../../api/queries/databases";
import { sitesQuery } from "../../api/queries/sites";
import type { SiteList } from "../../api/queries/sites";
import { countByOwner } from "./organization";
import { useOrganization } from "./useOrganization";

export interface Resources {
  apps: AppList["apps"];
  databases: DatabaseList["databases"];
  sites: SiteList["sites"];
  /** What each place holds, by `team/project` and `team/project/environment`, of the three kinds together. */
  held: ReadonlyMap<string, number>;
  /** The same, one kind at a time. */
  counts: { apps: ReadonlyMap<string, number>; databases: ReadonlyMap<string, number>; sites: ReadonlyMap<string, number> };
  /** Every list the principal may read has been read: a count is then the whole truth. */
  known: boolean;
  /** The lists are still being read for the first time. */
  loading: boolean;
}

const NONE: never[] = [];

/**
 * The applications, databases and sites of the server, as the pages of projects and teams need
 * them: grouped by where each lives, and counted. Each list is the one its own page reads (the
 * query cache is shared), and one the principal may not read is simply empty and does not hold
 * `known` back, since the server refuses to delete a project that is not empty whatever the
 * console counted.
 */
export function useResources(): Resources {
  const org = useOrganization();
  const apps = useQuery({ ...appsQuery(), enabled: org.available && org.can("apps.read"), retry: false, retryOnMount: false });
  const databases = useQuery({ ...databasesQuery(), enabled: org.available && org.can("databases.read"), retry: false, retryOnMount: false });
  const sites = useQuery({ ...sitesQuery(), enabled: org.available && org.can("apps.read"), retry: false, retryOnMount: false });

  return useMemo(() => {
    const appRows = apps.data?.apps ?? NONE;
    const databaseRows = databases.data?.databases ?? NONE;
    const siteRows = sites.data?.sites ?? NONE;
    // A list that is not being read (not the principal's to read) never will be: it is settled.
    const settled = (query: { isPending: boolean; fetchStatus: string }): boolean => !query.isPending || query.fetchStatus === "idle";
    const read = settled(apps) && settled(databases) && settled(sites);
    return {
      apps: appRows,
      databases: databaseRows,
      sites: siteRows,
      held: countByOwner([...appRows, ...databaseRows, ...siteRows]),
      counts: { apps: countByOwner(appRows), databases: countByOwner(databaseRows), sites: countByOwner(siteRows) },
      known: org.available && read,
      loading: org.loading || (org.available && !read),
    };
  }, [org.available, org.loading, apps, databases, sites]);
}
