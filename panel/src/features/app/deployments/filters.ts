/**
 * The deployments list's filters, which live in the URL (`?q=cart&state=failed&by=webhook`) so a
 * filtered history can be bookmarked, shared and reached with the back button. The store keeps the
 * last twenty deploys of an app, so they are filtered here, in the browser: there is no query for
 * the server to answer.
 */

import type { Deployment } from "../../apps/data";

/** What a deployment is for the filter: the version that serves is told apart from the ones that merely succeeded. */
export const DEPLOY_STATE_FILTERS = ["live", "running", "succeeded", "failed", "rolled_back", "cancelled"] as const;

export type DeployStateFilter = (typeof DEPLOY_STATE_FILTERS)[number];

/** What started a deployment, as the backend names it. */
export const STARTED_BY_FILTERS = ["webhook", "panel", "cli"] as const;

export type StartedByFilter = (typeof STARTED_BY_FILTERS)[number];

export interface DeploymentsSearch {
  /** Free text matched against the commit message, the commit and the branch. */
  q?: string;
  state?: DeployStateFilter;
  /** A branch exactly as the history records it. */
  branch?: string;
  by?: StartedByFilter;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

/** Reads the search params, dropping anything malformed: a stale link still opens the list. */
export function validateDeploymentsSearch(search: Record<string, unknown>): DeploymentsSearch {
  const q = text(search["q"]);
  const state = text(search["state"]);
  const branch = text(search["branch"]);
  const by = text(search["by"]);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(state !== undefined && (DEPLOY_STATE_FILTERS as readonly string[]).includes(state) ? { state: state as DeployStateFilter } : {}),
    ...(branch !== undefined ? { branch } : {}),
    ...(by !== undefined && (STARTED_BY_FILTERS as readonly string[]).includes(by) ? { by: by as StartedByFilter } : {}),
  };
}

/** Whether any filter narrows the list. */
export function isFiltered(search: DeploymentsSearch): boolean {
  return search.q !== undefined || search.state !== undefined || search.branch !== undefined || search.by !== undefined;
}

/** The state a deployment is filtered by. `liveId` is the deployment that serves now. */
export function stateOf(deployment: Pick<Deployment, "id" | "status" | "cancelled">, liveId: number | null): DeployStateFilter {
  if (deployment.cancelled) return "cancelled";
  if (deployment.id === liveId) return "live";
  switch (deployment.status) {
    case "queued":
    case "running":
    case "pending":
      return "running";
    case "failed":
      return "failed";
    case "rolled_back":
      return "rolled_back";
    default:
      return "succeeded";
  }
}

/** The branches the history has seen, in the order of the history, for the branch filter. */
export function branchesOf(deployments: readonly Pick<Deployment, "git_branch">[]): string[] {
  return [...new Set(deployments.flatMap((deployment) => (deployment.git_branch ? [deployment.git_branch] : [])))];
}

/** The deployments a search keeps, in their original order. */
export function filterDeployments(deployments: readonly Deployment[], search: DeploymentsSearch, liveId: number | null): Deployment[] {
  const needle = search.q?.toLowerCase();
  return deployments.filter((deployment) => {
    if (search.state !== undefined && stateOf(deployment, liveId) !== search.state) return false;
    if (search.branch !== undefined && deployment.git_branch !== search.branch) return false;
    if (search.by !== undefined && deployment.triggered_by !== search.by) return false;
    if (needle !== undefined) {
      const haystack = [deployment.commit_message, deployment.git_commit, deployment.git_branch, `#${String(deployment.id)}`].join("\n").toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
}
