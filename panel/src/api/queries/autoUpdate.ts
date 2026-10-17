/**
 * Noust installing its own new releases by itself (checklist N-28): the settings the operator
 * chose, the timer that follows them, and what the last run did. Reading it asks the network for
 * nothing; saving it writes or removes a systemd timer, in sudo mode.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type AutoUpdate = ResponseOf<"/api/system/update/auto", "get">;
export type AutoUpdateSettings = AutoUpdate["settings"];
export type AutoUpdateRun = NonNullable<AutoUpdate["last_run"]>;
export type AutoUpdateBody = BodyOf<"/api/system/update/auto", "put">;

export const autoUpdateKeys = {
  all: ["system", "auto-update"] as const,
};

export const autoUpdateQuery = () =>
  queryOptions({
    queryKey: autoUpdateKeys.all,
    queryFn: ({ signal }) => request("get", "/api/system/update/auto", { signal }),
  });

/** Saves the settings. Turning it on installs the timer; the answer is the state after. */
export function saveAutoUpdate(body: AutoUpdateBody): Promise<AutoUpdate> {
  return request("put", "/api/system/update/auto", { body });
}
