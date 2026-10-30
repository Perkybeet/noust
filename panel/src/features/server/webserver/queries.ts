/**
 * What the Web server section reads from `/api/webserver` (4.1, item 105): which web server holds
 * ports 80 and 443 and what is behind each, and the plan of moving sites to the other one. The
 * plan changes nothing; moving is a job.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../../api/client";
import type { BodyOf, ResponseOf } from "../../../api/client";

export type WebserverStatus = ResponseOf<"/api/webserver/status", "get">;
export type MovePlan = ResponseOf<"/api/webserver/migration/plan", "get">;
export type SitePlan = MovePlan["sites"][number];
export type Finding = NonNullable<SitePlan["findings"]>[number];
export type Sentence = MovePlan["confirmation"];
export type MoveBody = BodyOf<"/api/webserver/migrate", "post">;

export type WebServerName = "nginx" | "apache";

/** The capability a Noust older than 4.1 lacks: the section says so instead of failing on every call. */
export const WEBSERVER_CAPABILITY = "GET /api/webserver/status";

export const webserverKeys = {
  status: ["webserver", "status"] as const,
  plan: (to: WebServerName, domains: readonly string[] | null) => ["webserver", "plan", to, domains === null ? "all" : [...domains].sort()] as const,
};

/** Which web server holds the ports, what is behind each, and the setting new applications follow. */
export const webserverStatusQuery = () =>
  queryOptions({
    queryKey: webserverKeys.status,
    queryFn: ({ signal }) => request("get", "/api/webserver/status", { signal }),
  });

/**
 * What moving to `to` would do, for every site behind the other one (`domains` null) or for some.
 * Read when the person opens the move, never cached for long: it is a look at the files as they are.
 */
export const movePlanQuery = (to: WebServerName, domains: readonly string[] | null) =>
  queryOptions({
    queryKey: webserverKeys.plan(to, domains),
    queryFn: ({ signal }) => request("get", "/api/webserver/migration/plan", { query: domains === null ? { to, all: true } : { to, domain: [...domains] }, signal }),
    staleTime: 0,
    retry: false,
  });

/** Starts the move as a job. Sudo mode; `confirm` is the person agreeing to what the plan said. */
export function startMove(body: MoveBody): Promise<ResponseOf<"/api/webserver/migrate", "post">> {
  return request("post", "/api/webserver/migrate", { body });
}
