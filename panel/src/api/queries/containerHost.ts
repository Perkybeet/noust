/**
 * A central that deploys containers on its machine's own Docker (the container of a NAS, told so outside the console): the
 * check that says whether it can, and what it runs by itself where the machine has no timers. The console never turns it on.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** The report of `POST /api/central/container-host/check`: whether Noust can deploy here, check by check. */
export type DeployHereReport = ResponseOf<"/api/central/container-host/check", "post">;

/** One check: its key, its state (`ok`, `fail`, `warn` or `skipped`) and the sentence the server says for it, with a code. */
export type DeployHereCheck = DeployHereReport["checks"][number];

/** One thing the central runs itself at a time, where there are no timers. */
export type ScheduledTask = ResponseOf<"/api/central/schedule", "get">[number];

export const containerHostKeys = {
  schedule: ["central", "schedule"] as const,
};

/**
 * Asks the machine whether Noust can deploy on it. A POST, because the check writes a file and starts a throw-away container
 * (both removed): it is run by a person who opens the page or presses "Run again", never on every load.
 */
export function runDeployHereCheck(): Promise<DeployHereReport> {
  return request("post", "/api/central/container-host/check");
}

/** What the central runs itself and how each task stands: when it runs next, when it ran last, and what came due while it was off. */
export const centralScheduleQuery = () =>
  queryOptions({
    queryKey: containerHostKeys.schedule,
    queryFn: ({ signal }) => request("get", "/api/central/schedule", { signal }),
  });
