/**
 * Promoting a release to the same app in another environment (`GET /api/apps/{domain}/promotion`
 * and `POST .../promote`) and cloning an app (`POST .../clone`). Which apps can receive a release,
 * and what a clone is refused, are the server's rules; the console asks and shows the answer.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type Promotion = ResponseOf<"/api/apps/{domain}/promotion", "get">;
export type PromotionTarget = Promotion["targets"][number];
export type PromoteBody = BodyOf<"/api/apps/{domain}/promote", "post">;
export type CloneBody = BodyOf<"/api/apps/{domain}/clone", "post">;

export const promotionKey = (domain: string) => ["app", domain, "promotion"] as const;

/**
 * The release that serves an app and the apps it can be promoted to. Read when the dialog
 * opens, never cached as fresh: the target's own release may have changed a minute ago.
 */
export const promotionQuery = (domain: string) =>
  queryOptions({
    queryKey: promotionKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/promotion", { params: { domain }, signal }),
    staleTime: 0,
  });
