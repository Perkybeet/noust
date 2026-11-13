/**
 * What each credential did (`/api/auth/credentials` and `/api/auth/tokens/{id}/activity`, item 185): the tokens, the
 * built-in assistant (one credential for each person it works for) and the central's fleet tokens, the interventions each one made
 * as the audit trail tells them, and "Leave it as it was", which puts back the code of what a credential deployed. Nothing is
 * read from anywhere but the audit trail: with it off there is no activity.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type Credentials = ResponseOf<"/api/auth/credentials", "get">;
export type CredentialSummary = Credentials["credentials"][number];
export type ActivityList = ResponseOf<"/api/auth/tokens/{token_id}/activity", "get">;
export type Intervention = ActivityList["interventions"][number];
export type ActivityDetail = ResponseOf<"/api/auth/tokens/{token_id}/activity/{n}", "get">;
export type TimelineEntry = ActivityDetail["timeline"][number];
export type UndoPlan = ResponseOf<"/api/auth/tokens/{token_id}/activity/{n}/undo", "get">;
export type UndoItem = UndoPlan["items"][number];
export type UndoBody = BodyOf<"/api/auth/tokens/{token_id}/activity/{n}/undo", "post">;

export const credentialKeys = {
  all: ["credentials"] as const,
  list: ["credentials", "list"] as const,
  activity: (id: number, days: number) => ["credentials", "activity", id, { days }] as const,
  intervention: (id: number, n: number) => ["credentials", "intervention", id, n] as const,
  undo: (id: number, n: number) => ["credentials", "undo", id, n] as const,
};

export const credentialsQuery = () =>
  queryOptions({
    queryKey: credentialKeys.list,
    queryFn: ({ signal }) => request("get", "/api/auth/credentials", { signal }),
  });

export const activityQuery = (id: number, days = 30) =>
  queryOptions({
    queryKey: credentialKeys.activity(id, days),
    queryFn: ({ signal }) => request("get", "/api/auth/tokens/{token_id}/activity", { params: { token_id: id }, query: { days }, signal }),
  });

export const interventionQuery = (id: number, n: number) =>
  queryOptions({
    queryKey: credentialKeys.intervention(id, n),
    queryFn: ({ signal }) => request("get", "/api/auth/tokens/{token_id}/activity/{n}", { params: { token_id: id, n }, signal }),
  });

/** What leaving it as it was would do, application by application. It changes nothing. */
export const undoPlanQuery = (id: number, n: number) =>
  queryOptions({
    queryKey: credentialKeys.undo(id, n),
    queryFn: ({ signal }) => request("get", "/api/auth/tokens/{token_id}/activity/{n}/undo", { params: { token_id: id, n }, signal }),
    // The plan is made again when it is opened: what changed since somebody looked is judged as it is.
    staleTime: 0,
  });

/** Starts putting the code back. It is a job; it needs sudo mode, and it refuses with a conflict when a deployment it passes changed the schema and was not confirmed. */
export function undoIntervention(id: number, n: number, body: UndoBody) {
  return request("post", "/api/auth/tokens/{token_id}/activity/{n}/undo", { params: { token_id: id, n }, body });
}
