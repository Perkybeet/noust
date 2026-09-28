/**
 * Remote backup destinations: rclone-backed places a backup can be pushed to or restored
 * from. `queries/backups.ts` covers a backup's own record; this file is the destinations
 * they can travel to, and the `GET /backends` catalogue the "Add destination" form is built
 * from.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type BackendsResponse = ResponseOf<"/api/backup-destinations/backends", "get">;
export type Backend = BackendsResponse["backends"][number];
export type BackendField = Backend["fields"][number];
export type DestinationList = ResponseOf<"/api/backup-destinations", "get">;
export type Destination = DestinationList["destinations"][number];
export type RemoteBackups = ResponseOf<"/api/backup-destinations/{name}/backups", "get">;
export type RemoteBackup = NonNullable<RemoteBackups["backups"]>[number];
export type TestDestinationResult = ResponseOf<"/api/backup-destinations/{name}/test", "post">;
export type ShowKeyResult = ResponseOf<"/api/backup-destinations/{name}/show-key", "post">;

export const backupDestinationKeys = {
  all: ["backup-destinations"] as const,
  list: ["backup-destinations", "list"] as const,
  backends: ["backup-destinations", "backends"] as const,
  remote: (name: string, app: string | null) => ["backup-destinations", "remote", name, { app }] as const,
};

export const backupDestinationBackendsQuery = () =>
  queryOptions({
    queryKey: backupDestinationKeys.backends,
    queryFn: ({ signal }) => request("get", "/api/backup-destinations/backends", { signal }),
    // The catalogue never changes while the console is open: it is the server's own code,
    // not configuration, so there is nothing to invalidate it on.
    staleTime: Infinity,
  });

export const backupDestinationsQuery = () =>
  queryOptions({
    queryKey: backupDestinationKeys.list,
    queryFn: ({ signal }) => request("get", "/api/backup-destinations", { signal }),
  });

/** What a destination holds: applications at its top level, or one application's backups. */
export const remoteBackupsQuery = (name: string, app: string | null) =>
  queryOptions({
    queryKey: backupDestinationKeys.remote(name, app),
    queryFn: ({ signal }) =>
      request("get", "/api/backup-destinations/{name}/backups", {
        params: { name },
        query: app === null ? {} : { app },
        signal,
      }),
    enabled: name !== "",
  });
