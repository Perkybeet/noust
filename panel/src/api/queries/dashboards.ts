import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

/** The panels of Home an account keeps, and what a panel and an account may hold. */
export type Dashboards = ResponseOf<"/api/auth/dashboards", "get">;
export type DashboardOut = Dashboards["dashboards"][number];
export type DashboardBody = BodyOf<"/api/auth/dashboards/{dashboard_id}", "put">;

/**
 * Under `auth`, which the central owns whichever server is chosen (`CENTRAL_QUERY_ROOTS`): a person's panels
 * are one set across the whole fleet, never one for each server.
 */
export const dashboardKeys = {
  all: ["auth", "dashboards"] as const,
};

/**
 * The panels of the account asking, in the order it keeps them. The master token is no account and is answered
 * 400, which is the console's cue to keep that browser's panels in the browser (`features/overview/panels`),
 * so a refusal is never retried.
 */
export const dashboardsQuery = () =>
  queryOptions({
    queryKey: dashboardKeys.all,
    queryFn: ({ signal }) => request("get", "/api/auth/dashboards", { signal }),
    retry: false,
    // What is on screen is the person's own last save, kept in this browser; the server's answer confirms it.
    staleTime: 5 * 60_000,
  });

/** Creates a panel, or replaces the one the account has under that id; a new one goes last. */
export function saveDashboard(id: string, body: DashboardBody): Promise<DashboardOut> {
  return request("put", "/api/auth/dashboards/{dashboard_id}", { params: { dashboard_id: id }, body });
}

/** Deletes one of the account's panels and answers the ones that are left. */
export function deleteDashboard(id: string): Promise<Dashboards> {
  return request("delete", "/api/auth/dashboards/{dashboard_id}", { params: { dashboard_id: id } });
}
