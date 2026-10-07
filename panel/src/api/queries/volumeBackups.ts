/**
 * An application's data (its persistent directories and the Docker volumes of its stack),
 * backed up on its own: the policies that schedule it, the backups taken, and the pieces of
 * data an application declares. Taking, checking, sending and restoring are jobs the pages
 * start; a policy and a restore ask for sudo mode.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type VolumePolicyList = ResponseOf<"/api/volume-backups/policies", "get">;
export type VolumePolicy = VolumePolicyList["policies"][number];
export type VolumePolicyBody = BodyOf<"/api/volume-backups/policies/{domain}", "put">;
export type VolumeBackupList = ResponseOf<"/api/volume-backups", "get">;
export type VolumeBackup = VolumeBackupList["backups"][number];
export type VolumeTarget = ResponseOf<"/api/volume-backups/targets/{domain}", "get">[number];

/** How still the data is kept while it is copied. */
export const CONSISTENCY_MODES = ["live", "pause", "stop"] as const;
export type Consistency = (typeof CONSISTENCY_MODES)[number];

export const volumeKeys = {
  all: ["volume-backups"] as const,
  policies: ["volume-backups", "policies"] as const,
  backups: (domain: string | null) => ["volume-backups", "backups", domain] as const,
  targets: (domain: string, paths: readonly string[]) => ["volume-backups", "targets", domain, paths] as const,
};

/** Every application's data backup policy. */
export const volumePoliciesQuery = () =>
  queryOptions({
    queryKey: volumeKeys.policies,
    queryFn: ({ signal }) => request("get", "/api/volume-backups/policies", { signal }),
  });

/** The data backups on this server, newest first; of one application, or of all. */
export const volumeBackupsQuery = (domain: string | null = null) =>
  queryOptions({
    queryKey: volumeKeys.backups(domain),
    queryFn: ({ signal }) => request("get", "/api/volume-backups", { query: domain === null ? {} : { domain }, signal }),
  });

/** The data an application declares, and whether each piece exists yet; with directories added to it. */
export const volumeTargetsQuery = (domain: string, paths: readonly string[] = []) =>
  queryOptions({
    queryKey: volumeKeys.targets(domain, paths),
    queryFn: ({ signal }) => request("get", "/api/volume-backups/targets/{domain}", { params: { domain }, query: { path: [...paths] }, signal }),
    staleTime: 15_000,
  });
