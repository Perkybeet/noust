import type { QueryClient } from "@tanstack/react-query";

import { appQuery } from "../../api/queries/apps";
import { deploymentLogQuery, deploymentQuery, deploymentsQuery } from "../../api/queries/deployments";
import { activeJobsQuery } from "../../api/queries/jobs";
import { pendingQuery } from "../../api/queries/runtime";
import { preloadResourceColumns } from "../../app/frame/resourceColumnChunks";
import { RECENT_APP_DEPLOYS } from "./AppBanner";

/** A read that failed is the page's to say, not the one that reads ahead of it. */
function ignoreFailure(): undefined {
  return undefined;
}

/**
 * What an application's page reads before it can draw its header and its tabs (`AppLayout`: the application, its newest
 * deployments, and what is not applied yet), the column of its sections beside it (a chunk of its own the first time an
 * application is entered, which drew the column in loading shapes next to a page that was already there) and the jobs that
 * run, read afresh: a page that does not yet know its first deploy is running draws the overview of an application that has
 * run (its history and its logs, in loading shapes) and a moment later the deploy that is. Whoever sends the operator there
 * reads it first, so the page arrives drawn and not in the shapes of what it is waiting for. A read that fails is the page's
 * to say: this never rejects.
 */
export function readAppPage(queryClient: QueryClient, domain: string): Promise<unknown[]> {
  return Promise.all([
    queryClient.query(appQuery(domain)).catch(ignoreFailure),
    queryClient.query(deploymentsQuery({ domain, limit: RECENT_APP_DEPLOYS })).catch(ignoreFailure),
    queryClient.query(pendingQuery(domain)).catch(ignoreFailure),
    // The list the task centre keeps is as old as the last event it heard, and the job the operator has just queued may not be in it yet.
    queryClient.query({ ...activeJobsQuery(), staleTime: 0 }).catch(ignoreFailure),
    preloadResourceColumns().catch(ignoreFailure),
  ]);
}

/**
 * What a deployment's page reads before it can draw the deployment and its build log, as `readAppPage` does for the
 * application around it.
 */
export function readDeploymentPage(queryClient: QueryClient, id: number): Promise<unknown[]> {
  return Promise.all([queryClient.query(deploymentQuery(id)).catch(ignoreFailure), queryClient.query(deploymentLogQuery(id, null)).catch(ignoreFailure)]);
}
