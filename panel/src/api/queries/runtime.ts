import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type HealthDetails = ResponseOf<"/api/apps/{domain}/health", "get">;
export type PendingChanges = ResponseOf<"/api/apps/{domain}/pending", "get">;
export type PendingItem = NonNullable<PendingChanges["items"]>[number];
export type Hardening = ResponseOf<"/api/apps/{domain}/hardening", "get">;
export type UnitTuning = ResponseOf<"/api/apps/{domain}/tuning", "get">;
export type Replicas = ResponseOf<"/api/apps/{domain}/replicas", "get">;
export type ReplicaCopy = Replicas["units"][number];
export type Processes = ResponseOf<"/api/apps/{domain}/processes", "get">;
export type AppProcess = Processes["processes"][number];
export type Mounts = ResponseOf<"/api/apps/{domain}/mounts", "get">;
export type Mount = Mounts["mounts"][number];
export type FileListing = ResponseOf<"/api/apps/{domain}/files", "get">;
export type FileEntry = FileListing["entries"][number];
export type LogDrains = ResponseOf<"/api/log-drains", "get">;
export type LogDrain = LogDrains["drains"][number];

/**
 * Under the app's own prefix (`["app", domain, ...]`), like everything read about one app, so a
 * job on the app finishing (a deploy, a restart, the scale that was asked for) refreshes these
 * with the rest.
 */
export const runtimeKeys = {
  health: (domain: string) => ["app", domain, "health-check"] as const,
  pending: (domain: string) => ["app", domain, "pending"] as const,
  hardening: (domain: string) => ["app", domain, "hardening"] as const,
  tuning: (domain: string) => ["app", domain, "tuning"] as const,
  replicas: (domain: string) => ["app", domain, "replicas"] as const,
  processes: (domain: string) => ["app", domain, "processes"] as const,
  mounts: (domain: string) => ["app", domain, "mounts"] as const,
  files: (domain: string, path: string) => ["app", domain, "files", path] as const,
  logDrains: ["log-drains"] as const,
};

/** The whole startup check: how it asks, what it asks and how long it waits. Changes nothing. */
export const healthDetailsQuery = (domain: string) =>
  queryOptions({
    queryKey: runtimeKeys.health(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/health", { params: { domain }, signal }),
  });

/** What the application is set to that it does not run with yet, and what applying each takes. */
export const pendingQuery = (domain: string) =>
  queryOptions({
    queryKey: runtimeKeys.pending(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/pending", { params: { domain }, signal }),
  });

/** How hardened the application's unit is, and what systemd scores it. */
export const hardeningQuery = (domain: string) =>
  queryOptions({
    queryKey: runtimeKeys.hardening(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/hardening", { params: { domain }, signal }),
  });

/** How the application's unit is tuned beyond its hard limits, and what can be asked for. */
export const tuningQuery = (domain: string) =>
  queryOptions({
    queryKey: runtimeKeys.tuning(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/tuning", { params: { domain }, signal }),
  });

/** The copies of the web process, and whether the application can run as more than one. */
export const replicasQuery = (domain: string) =>
  queryOptions({
    queryKey: runtimeKeys.replicas(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/replicas", { params: { domain }, signal }),
  });

/** The processes the application runs beside its web process, with the state of each copy. */
export const processesQuery = (domain: string) =>
  queryOptions({
    queryKey: runtimeKeys.processes(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/processes", { params: { domain }, signal }),
  });

/** The files and folders the application is given beyond its repository. */
export const mountsQuery = (domain: string) =>
  queryOptions({
    queryKey: runtimeKeys.mounts(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/mounts", { params: { domain }, signal }),
  });

/** One folder of the application's tree, as the application's own account sees it. */
export const filesQuery = (domain: string, path: string) =>
  queryOptions({
    queryKey: runtimeKeys.files(domain, path),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/files", { params: { domain }, query: { path }, signal }),
  });

/** Where the logs of applications are shipped to, and how the last attempt at each went. */
export const logDrainsQuery = () =>
  queryOptions({
    queryKey: runtimeKeys.logDrains,
    queryFn: ({ signal }) => request("get", "/api/log-drains", { signal }),
  });
