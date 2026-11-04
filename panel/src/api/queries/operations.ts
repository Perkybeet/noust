import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** What the server does that is not a job: the long operations that answer on their own request. */
export type Operations = ResponseOf<"/api/operations/active", "get">;
export type Operation = Operations["operations"][number];

/** What every server of the fleet is running: a row each, with its jobs and its operations. */
export type FleetActive = ResponseOf<"/api/fleet/jobs/active", "get">;

/**
 * Under `["jobs"]`, the prefix every job read is under, so a job that starts or ends (the `job` event
 * invalidates the prefix) reads these again with the lists. An operation has no event of its own in the
 * console yet: while one runs it is read again every few seconds, and when none does it is not.
 */
export const operationKeys = {
  active: ["jobs", "operations"] as const,
  fleet: ["jobs", "fleet-active"] as const,
};

/** How often what is running is read again while anything is. */
export const OPERATIONS_POLL_MS = 3_000;

/** How often the fleet's work is read again: a central asks each server, so it is not asked every few seconds. */
export const FLEET_ACTIVE_POLL_MS = 15_000;

/** The long operations running on this server now, oldest first. */
export const operationsActiveQuery = () =>
  queryOptions({
    queryKey: operationKeys.active,
    queryFn: ({ signal }) => request("get", "/api/operations/active", { signal }),
    refetchInterval: (query) => ((query.state.data?.operations.length ?? 0) > 0 ? OPERATIONS_POLL_MS : false),
    // A server older than this console has no such route: the jobs alone are what it has to say.
    retry: false,
  });

/** What each server of the fleet is running, jobs and operations, as the central puts it together. */
export const fleetActiveQuery = () =>
  queryOptions({
    queryKey: operationKeys.fleet,
    queryFn: ({ signal }) => request("get", "/api/fleet/jobs/active", { signal }),
    refetchInterval: FLEET_ACTIVE_POLL_MS,
    retry: false,
  });
