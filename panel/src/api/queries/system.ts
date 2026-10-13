import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** The machine snapshot: the topbar strip, the `machine` server event, GET /api/system/machine. */
export type Machine = ResponseOf<"/api/system/machine", "get">;
export type SystemInfo = ResponseOf<"/api/system", "get">;
export type SystemHealth = ResponseOf<"/api/system/health", "get">;
export type NetworkInfo = ResponseOf<"/api/system/network", "get">;
export type ProcessList = ResponseOf<"/api/system/processes", "get">;
export type InstallCheck = ResponseOf<"/api/system/doctor", "get">;
export type Changelog = ResponseOf<"/api/system/changelog", "get">;
export type ChangelogEntry = Changelog["entries"][number];
export type ReleaseNotes = ResponseOf<"/api/system/release-notes", "get">;
export type SupportReport = ResponseOf<"/api/system/support-report", "get">;

export const systemKeys = {
  all: ["system"] as const,
  machine: ["system", "machine"] as const,
  info: ["system", "info"] as const,
  version: ["system", "version"] as const,
  health: ["system", "health"] as const,
  installCheck: ["system", "doctor"] as const,
  changelog: (limit: number) => ["system", "changelog", { limit }] as const,
  releaseNotes: (version: string) => ["system", "release-notes", version] as const,
  network: ["system", "network"] as const,
  processes: (sortBy: string, limit: number) => ["system", "processes", { sortBy, limit }] as const,
};

/**
 * The machine snapshot. The REST read paints the strip once; after that the `machine` server
 * event writes into this same entry every five seconds, so nothing polls while the stream
 * is up.
 */
export const machineQuery = () =>
  queryOptions({
    queryKey: systemKeys.machine,
    queryFn: ({ signal }) => request("get", "/api/system/machine", { signal }),
    staleTime: 30_000,
  });

export const systemInfoQuery = () =>
  queryOptions({
    queryKey: systemKeys.info,
    queryFn: ({ signal }) => request("get", "/api/system", { signal }),
  });

export const versionQuery = () =>
  queryOptions({
    queryKey: systemKeys.version,
    queryFn: ({ signal }) => request("get", "/api/system/version", { signal }),
    staleTime: 60 * 60_000,
  });

/** The same verdict and checks `noust health` prints, as the Server page's health card. */
export const systemHealthQuery = () =>
  queryOptions({
    queryKey: systemKeys.health,
    queryFn: ({ signal }) => request("get", "/api/system/health", { signal }),
  });

export const networkQuery = () =>
  queryOptions({
    queryKey: systemKeys.network,
    queryFn: ({ signal }) => request("get", "/api/system/network", { signal }),
  });

export const processesQuery = (sortBy: "cpu" | "memory" | "pid" | "name" = "cpu", limit = 25) =>
  queryOptions({
    queryKey: systemKeys.processes(sortBy, limit),
    queryFn: ({ signal }) => request("get", "/api/system/processes", { query: { sort_by: sortBy, limit }, signal }),
  });

/** What `noust setup doctor` finds: the installation check of Settings > About. */
export const installCheckQuery = () =>
  queryOptions({
    queryKey: systemKeys.installCheck,
    queryFn: ({ signal }) => request("get", "/api/system/doctor", { signal }),
    staleTime: 30_000,
  });

/** What changed in the versions this installation ships with, newest first. */
export const changelogQuery = (limit: number) =>
  queryOptions({
    queryKey: systemKeys.changelog(limit),
    queryFn: ({ signal }) => request("get", "/api/system/changelog", { query: { limit }, signal }),
    staleTime: 60 * 60_000,
  });

/**
 * What an update to a version will change, read by the server from that release on GitHub. Asked
 * only when the operator opens it: the server keeps the answer an hour, and a failure is an answer
 * (`available: false` with the reason), never an error.
 */
export const releaseNotesQuery = (version: string, enabled: boolean) =>
  queryOptions({
    queryKey: systemKeys.releaseNotes(version),
    queryFn: ({ signal }) => request("get", "/api/system/release-notes", { query: { version }, signal }),
    enabled,
    staleTime: 60 * 60_000,
  });
