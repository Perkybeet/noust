import type { QueryClient } from "@tanstack/react-query";

import { appQuery } from "../../api/queries/apps";
import { deploymentLogQuery, deploymentQuery, deploymentsQuery } from "../../api/queries/deployments";
import { pendingQuery } from "../../api/queries/runtime";
import { RECENT_APP_DEPLOYS } from "./AppBanner";

/** A read that failed is the page's to say, not the one that reads ahead of it. */
function ignoreFailure(): undefined {
  return undefined;
}

/**
 * What an application's page reads before it can draw its header and its tabs (`AppLayout`: the application, its newest
 * deployments, and what is not applied yet). Whoever sends the operator there reads it first, so the page arrives drawn and
 * not in the shapes of what it is waiting for. A read that fails is the page's to say: this never rejects.
 */
export function readAppPage(queryClient: QueryClient, domain: string): Promise<unknown[]> {
  return Promise.all([
    queryClient.query(appQuery(domain)).catch(ignoreFailure),
    queryClient.query(deploymentsQuery({ domain, limit: RECENT_APP_DEPLOYS })).catch(ignoreFailure),
    queryClient.query(pendingQuery(domain)).catch(ignoreFailure),
  ]);
}

/**
 * What a deployment's page reads before it can draw the deployment and its build log, as `readAppPage` does for the
 * application around it.
 */
export function readDeploymentPage(queryClient: QueryClient, id: number): Promise<unknown[]> {
  return Promise.all([queryClient.query(deploymentQuery(id)).catch(ignoreFailure), queryClient.query(deploymentLogQuery(id, null)).catch(ignoreFailure)]);
}
