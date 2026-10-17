/**
 * The probes: addresses the monitor asks from outside the application, where each stands, and
 * the public status page made of the ones marked public. Publishing and taking the page off a
 * name are jobs that ask for sudo mode.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type ProbeList = ResponseOf<"/api/probes", "get">;
export type Probe = ProbeList["items"][number];
export type ProbeStatus = Probe["status"];
export type StatusPage = ProbeList["status_page"];
export type ProbeBody = BodyOf<"/api/probes/{name}", "put">;
export type StatusPageBody = BodyOf<"/api/probes/status-page", "put">;
export type PublishBody = BodyOf<"/api/probes/status-page/publish", "post">;
export type ProbeHistory = ResponseOf<"/api/probes/{name}/history", "get">;
export type ProbeCheck = ResponseOf<"/api/probes/{name}/check", "post">;

export const probeKeys = {
  all: ["probes"] as const,
  history: (name: string) => ["probes", "history", name] as const,
};

/** The probes and the status page. Read again every half minute while the page is open. */
export const probesQuery = () =>
  queryOptions({
    queryKey: probeKeys.all,
    queryFn: ({ signal }) => request("get", "/api/probes", { signal }),
    refetchInterval: 30_000,
  });

/** A probe's newest incidents and answers. */
export const probeHistoryQuery = (name: string) =>
  queryOptions({
    queryKey: probeKeys.history(name),
    queryFn: ({ signal }) => request("get", "/api/probes/{name}/history", { params: { name }, signal }),
  });

/** Creates a probe or replaces the one of that name. */
export function saveProbe(name: string, body: ProbeBody) {
  return request("put", "/api/probes/{name}", { params: { name }, body });
}

export function removeProbe(name: string) {
  return request("delete", "/api/probes/{name}", { params: { name } });
}

/** Asks a probe now and answers with what it got. */
export function checkProbe(name: string) {
  return request("post", "/api/probes/{name}/check", { params: { name } });
}

/** Changes what is given of the status page; what is left out is kept. Needs sudo mode. */
export function saveStatusPage(body: StatusPageBody) {
  return request("put", "/api/probes/status-page", { body });
}

/** Writes the page's files now. */
export function writeStatusPage() {
  return request("post", "/api/probes/status-page/write");
}

/** Puts the page on a name of its own (a job). Needs sudo mode. */
export function publishStatusPage(body: PublishBody) {
  return request("post", "/api/probes/status-page/publish", { body });
}

/** Takes the page off its name (a job), removing exactly what publishing made. Needs sudo mode. */
export function unpublishStatusPage() {
  return request("delete", "/api/probes/status-page/site");
}
