/**
 * What is read again when something about teams, projects or environments changes, in one
 * place so no screen forgets a list that names where things live.
 */

import type { QueryClient } from "@tanstack/react-query";

import { appKeys } from "../../api/queries/apps";
import { databaseKeys } from "../../api/queries/databases";
import { siteKeys } from "../../api/queries/sites";
import { teamKeys } from "../../api/queries/teams";

/** A team, a member, a project or an environment changed: every list of them is read again. */
export function refreshOrganization(queryClient: QueryClient): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: teamKeys.all }),
    queryClient.invalidateQueries({ queryKey: teamKeys.projects }),
    queryClient.invalidateQueries({ queryKey: teamKeys.environments }),
  ]);
}

/**
 * An application moved: it, its sites and its databases live somewhere else now, so every list
 * that says where is read again, along with what each project and team holds.
 */
export function refreshPlacement(queryClient: QueryClient, domain: string): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: appKeys.list }),
    queryClient.invalidateQueries({ queryKey: appKeys.detail(domain) }),
    queryClient.invalidateQueries({ queryKey: siteKeys.all }),
    queryClient.invalidateQueries({ queryKey: databaseKeys.lists }),
    refreshOrganization(queryClient),
  ]);
}
