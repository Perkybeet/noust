/**
 * The copies a central holds of its servers: what it has taken through the tunnel it already
 * has, how collecting is going with each server and what its knobs are. The servers' own
 * backups are `queries/backups.ts`; this is the central's second place for them.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type CentralCopies = ResponseOf<"/api/central-backups", "get">;
export type CentralCopy = CentralCopies["items"][number];
export type CollectingStatus = ResponseOf<"/api/central-backups/status", "get">;
export type CollectingNode = CollectingStatus["nodes"][number];
export type CollectSettingsBody = BodyOf<"/api/central-backups/settings", "put">;
export type GiveBackBody = BodyOf<"/api/central-backups/{node}/{kind}/restore", "post">;

export const centralBackupKeys = {
  all: ["central-backups"] as const,
  copies: ["central-backups", "copies"] as const,
  status: ["central-backups", "status"] as const,
};

/** How often what is moving is read again while the central is taking something. */
export const COLLECTING_POLL_MS = 4_000;

/** The copies the central holds, newest first. */
export const centralCopiesQuery = (enabled = true) =>
  queryOptions({
    queryKey: centralBackupKeys.copies,
    queryFn: ({ signal }) => request("get", "/api/central-backups", { signal }),
    enabled,
  });

/**
 * How collecting is going: who is followed, each server's state, what is moving and the disk. It is read again on
 * its own while a server is moving a copy, because nothing else tells the console that the central is busy.
 */
export const collectingStatusQuery = (enabled = true) =>
  queryOptions({
    queryKey: centralBackupKeys.status,
    queryFn: ({ signal }) => request("get", "/api/central-backups/status", { signal }),
    enabled,
    refetchInterval: (query) => (query.state.data?.nodes.some((node) => node.moving !== null && node.moving !== undefined) === true ? COLLECTING_POLL_MS : false),
  });
