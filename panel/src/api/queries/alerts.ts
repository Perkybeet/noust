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

/**
 * Takes an alert that is firing: its reminders stop, the channels are told who took it and a pager
 * acknowledges the incident. `subject` is the application, or empty for the server's.
 */
export function acknowledgeAlert(name: string, subject: string) {
  return request("post", "/api/alerts/rules/{name}/acknowledge", { params: { name }, query: { subject } });
}

/** Hands a taken alert back: its reminder speaks again. */
export function handBackAlert(name: string, subject: string) {
  return request("delete", "/api/alerts/rules/{name}/acknowledge", { params: { name }, query: { subject } });
}

export function switchAlertRule(name: string, enabled: boolean) {
  return enabled
    ? request("post", "/api/alerts/rules/{name}/enable", { params: { name } })
    : request("post", "/api/alerts/rules/{name}/disable", { params: { name } });
}
