/**
 * Noust's own state in one encrypted file: the bundles on this server, the schedule that takes
 * them and how it is going. The console takes, checks, sends and deletes bundles and keeps the
 * schedule; it never restores one (that stops the console while the store it runs on is
 * replaced, so it is a command at a terminal) and never downloads one (a bundle holds every
 * secret the server has).
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type SelfBackupOverview = ResponseOf<"/api/self-backup", "get">;
export type Bundle = SelfBackupOverview["bundles"][number];
export type SelfSchedule = SelfBackupOverview["schedule"];
export type SelfScheduleBody = BodyOf<"/api/self-backup/schedule", "put">;

export const selfBackupKeys = {
  all: ["self-backup"] as const,
  overview: ["self-backup", "overview"] as const,
};

/** The bundles, newest first, and the schedule with its timer. */
export const selfBackupQuery = () =>
  queryOptions({
    queryKey: selfBackupKeys.overview,
    queryFn: ({ signal }) => request("get", "/api/self-backup", { signal }),
  });
