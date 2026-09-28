import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type Previews = ResponseOf<"/api/apps/{domain}/previews", "get">;
export type Preview = Previews["previews"][number];
export type PreviewSettings = NonNullable<Previews["settings"]>;

/** Under the parent app's own prefix, so a job on the app finishing refreshes it too. */
export const previewsKey = (domain: string) => ["app", domain, "previews"] as const;

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
