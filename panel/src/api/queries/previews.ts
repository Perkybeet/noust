import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type Previews = ResponseOf<"/api/apps/{domain}/previews", "get">;
export type Preview = Previews["previews"][number];
export type PreviewSettings = NonNullable<Previews["settings"]>;
export type PullRequests = ResponseOf<"/api/apps/{domain}/previews/pull-requests", "get">;
export type PullRequest = PullRequests["pull_requests"][number];
export type PreviewLogs = ResponseOf<"/api/apps/{domain}/previews/{number}/logs", "get">;
export type PreviewVariables = ResponseOf<"/api/apps/{domain}/previews/variables", "get">;
export type PreviewVariable = PreviewVariables["variables"][number];

/** Under the parent app's own prefix, so a job on the app finishing refreshes it too. */
export const previewsKey = (domain: string) => ["app", domain, "previews"] as const;

/** The variables defined for an app's previews, under the same prefix so a job on the app refreshes them. */
export const previewVariablesKey = (domain: string) => ["app", domain, "previews", "variables"] as const;

/** The open pull requests of the app's repository, read when the dialog that lists them opens. */
export const pullRequestsKey = (domain: string) => ["app", domain, "previews", "pull-requests"] as const;

/** What a preview printed: its last build and its journal. */
export const previewLogsKey = (domain: string, number: number) => ["app", domain, "previews", "logs", number] as const;

/** The preview states that change without the operator doing anything: a build, a removal. */
const MOVING = new Set(["pending", "deploying", "removing"]);

/** How often the list is read again while a preview is on its way somewhere. */
export const PREVIEWS_POLL_MS = 5_000;

/**
 * When to read the previews again: every few seconds while one is being built or removed.
 * Their jobs name the preview's own domain, not the parent's, so a job event finishing does
 * not refresh this list; the poll is what shows a build landing.
 */
export function previewsInterval(data: Pick<Previews, "previews"> | undefined): number | false {
  return data?.previews.some((preview) => MOVING.has(preview.status)) ? PREVIEWS_POLL_MS : false;
}

/** An app's preview settings (null when previews are off) and its previews, oldest first. */
export const previewsQuery = (domain: string) =>
  queryOptions({
    queryKey: previewsKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/previews", { params: { domain }, signal }),
    refetchInterval: (query) => previewsInterval(query.state.data),
  });

/**
 * The open pull requests of the app's repository, each with the preview it already has. Needs
 * this server's GitHub App; on any other host the server says so and the dialog offers a branch.
 * Never cached as fresh: a pull request opened a minute ago is the one being looked for.
 */
export const pullRequestsQuery = (domain: string, enabled: boolean) =>
  queryOptions({
    queryKey: pullRequestsKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/previews/pull-requests", { params: { domain }, signal }),
    enabled,
    staleTime: 0,
    retry: false,
  });

/** What one preview's process printed, and the log of its last build. */
export const previewLogsQuery = (domain: string, number: number, lines = 200) =>
  queryOptions({
    queryKey: [...previewLogsKey(domain, number), { lines }] as const,
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/previews/{number}/logs", { params: { domain, number }, query: { lines }, signal }),
    staleTime: 0,
  });

/** The variables defined for previews alone, a secret's value masked. */
export const previewVariablesQuery = (domain: string) =>
  queryOptions({
    queryKey: previewVariablesKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/previews/variables", { params: { domain }, signal }),
  });
