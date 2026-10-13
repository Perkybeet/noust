import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type AlertRuleList = ResponseOf<"/api/alerts/rules", "get">;
export type AlertRule = AlertRuleList["items"][number];
export type AlertMetric = AlertRuleList["metrics"][number];
export type AlertRuleBody = BodyOf<"/api/alerts/rules/{name}", "put">;

export const alertKeys = {
  rules: ["alerts", "rules"] as const,
};

/** The rules, what each one watches, and where each stands. Read again while the page is open. */
export const alertRulesQuery = () =>
  queryOptions({
    queryKey: alertKeys.rules,
    queryFn: ({ signal }) => request("get", "/api/alerts/rules", { signal }),
    refetchInterval: 30_000,
  });

/** Creates a rule or replaces the one of that name. The limit may be text such as `90%` or `512MB`. */
export function saveAlertRule(name: string, body: AlertRuleBody) {
  return request("put", "/api/alerts/rules/{name}", { params: { name }, body });
}

export function removeAlertRule(name: string) {
  return request("delete", "/api/alerts/rules/{name}", { params: { name } });
}

export function switchAlertRule(name: string, enabled: boolean) {
  return enabled
    ? request("post", "/api/alerts/rules/{name}/enable", { params: { name } })
    : request("post", "/api/alerts/rules/{name}/disable", { params: { name } });
}
