import { queryOptions } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** How an application is delivered: its history, its stop policy, its pause and the freeze over it. */
export type Rollout = ResponseOf<"/api/apps/{domain}/rollout", "get">;
export type Freeze = NonNullable<Rollout["freeze"]>;
export type Freezes = ResponseOf<"/api/deploy-freezes", "get">;
export type UpdateSchedule = ResponseOf<"/api/apps/{domain}/update-schedule", "get">;

/** Under the app's own prefix like everything read about one app, so a job on it refreshes these. */
export const rolloutKey = (domain: string) => ["app", domain, "rollout"] as const;
export const updateScheduleKey = (domain: string) => ["app", domain, "update-schedule"] as const;

/** The freezes of the whole server and of each application, read together: one list, filtered where it is shown. */
export const freezesKey = ["deploy-freezes"] as const;

/**
 * Reads again every freeze and every app's delivery settings: a freeze of the whole server
 * changes what each app's `rollout` says, so the apps are not told apart here.
 */
export function invalidateHolds(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: freezesKey });
  void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] === "app" && query.queryKey[2] === "rollout" });
}

/** Delivery settings, the pause of what deploys by itself, and the freeze that holds deploys now, if any. */
export const rolloutQuery = (domain: string) =>
  queryOptions({
    queryKey: rolloutKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/rollout", { params: { domain }, signal }),
  });

/** The freezes that hold deploys now or start later; the ones that ended are not asked for. */
export const freezesQuery = () =>
  queryOptions({
    queryKey: freezesKey,
    queryFn: ({ signal }) => request("get", "/api/deploy-freezes", { signal }),
  });

/** When the app updates itself, and how the last run ended. */
export const updateScheduleQuery = (domain: string) =>
  queryOptions({
    queryKey: updateScheduleKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/update-schedule", { params: { domain }, signal }),
  });
