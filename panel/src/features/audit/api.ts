/**
 * The audit trail's health and review (`/api/audit/verify`, `/status`, `/events`, `/reviews`)
 * and the compliance check (`/api/ens`), for the Audit and Compliance pages. The events
 * themselves are `api/queries/audit.ts`'s, shared with Activity.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";

export type AuditVerify = ResponseOf<"/api/audit/verify", "get">;
export type AuditStatus = ResponseOf<"/api/audit/status", "get">;
export type AuditCatalog = ResponseOf<"/api/audit/events", "get">;
export type AuditReviews = ResponseOf<"/api/audit/reviews", "get">;
export type AuditReview = AuditReviews["items"][number];
export type ReviewBody = BodyOf<"/api/audit/reviews", "post">;
export type EnsCheck = ResponseOf<"/api/ens/check", "get">;
export type EnsFinding = EnsCheck["findings"][number];
export type Lockdown = ResponseOf<"/api/ens/incident", "get">;
export type AccessReview = ResponseOf<"/api/ens/access-review", "get">;

export const trailKeys = {
  verify: ["audit", "verify"] as const,
  status: ["audit", "status"] as const,
  catalog: ["audit", "catalog"] as const,
  reviews: ["audit", "reviews"] as const,
  check: ["ens", "check"] as const,
  incident: ["ens", "incident"] as const,
  accessReview: ["ens", "access-review"] as const,
};

/** Whether the chain of events is intact, from the first to the last. */
export const verifyQuery = () =>
  queryOptions({ queryKey: trailKeys.verify, queryFn: ({ signal }) => request("get", "/api/audit/verify", { signal }), staleTime: 60_000 });

/** Whether events are written and shipped: the key, the size, every destination. */
export const statusQuery = () =>
  queryOptions({ queryKey: trailKeys.status, queryFn: ({ signal }) => request("get", "/api/audit/status", { signal }), staleTime: 30_000 });

/** The closed catalog: every event name and its category. */
export const catalogQuery = () =>
  queryOptions({ queryKey: trailKeys.catalog, queryFn: ({ signal }) => request("get", "/api/audit/events", { signal }), staleTime: Infinity });

export const reviewsQuery = () =>
  queryOptions({ queryKey: trailKeys.reviews, queryFn: ({ signal }) => request("get", "/api/audit/reviews", { query: { limit: 5 }, signal }) });

export function recordReview(body: ReviewBody) {
  return request("post", "/api/audit/reviews", { body });
}

/** The compliance check against the ENS category MEDIUM profile. */
export const checkQuery = (refresh = false) =>
  queryOptions({
    queryKey: [...trailKeys.check, refresh] as const,
    queryFn: ({ signal }) => request("get", "/api/ens/check", { query: refresh ? { refresh } : {}, signal }),
    staleTime: 60_000,
  });

/** Whether the console is locked down for an incident. */
export const incidentQuery = () =>
  queryOptions({ queryKey: trailKeys.incident, queryFn: ({ signal }) => request("get", "/api/ens/incident", { signal }), staleTime: 30_000 });

/** The list a security officer attests in an access review, and the past attestations. */
export const accessReviewQuery = () =>
  queryOptions({ queryKey: trailKeys.accessReview, queryFn: ({ signal }) => request("get", "/api/ens/access-review", { signal }) });

export function attestAccessReview(digest: string, notes: string) {
  return request("post", "/api/ens/access-review", { body: { digest, notes } });
}
