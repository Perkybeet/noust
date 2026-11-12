import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type FirstDeploys = ResponseOf<"/api/first-deploys", "get">;
/** One first deployment that failed and left no application behind. */
export type FirstDeploy = FirstDeploys["items"][number];

/**
 * Keys of the first deployments that failed. The realtime layer invalidates `all` whenever a job changes
 * status: the failure is the job's, and it is found a moment after the job ends.
 */
export const firstDeployKeys = {
  all: ["first-deploys"] as const,
  list: (domain: string | null) => ["first-deploys", "list", domain] as const,
};

/**
 * The first deployments that failed, all of them or one domain's. A server older than this console has no such
 * route and answers 404: that is not worth a second try, and the page of the domain says there is nothing.
 */
export const firstDeploysQuery = (domain: string | null = null) =>
  queryOptions({
    queryKey: firstDeployKeys.list(domain),
    queryFn: ({ signal }) => request("get", "/api/first-deploys", { query: domain === null ? {} : { domain }, signal }),
    retry: false,
  });

/** Puts a failed first deployment away: it stops being listed here and in what needs attention. */
export function dismissFirstDeploy(attempt: string) {
  return request("post", "/api/first-deploys/{attempt}/dismiss", { params: { attempt } });
}
