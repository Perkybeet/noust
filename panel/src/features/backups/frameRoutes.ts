/**
 * The fake API the Backups area's frame reads in a test: the header's storage summary and the
 * counts on its tabs. A tab's own test adds the routes of what it exercises.
 */

import { json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

export function frameRoutes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/backups": () => json(200, { backups: [], total: 0 }),
    "GET /api/backups/storage": () =>
      json(200, {
        path: "/var/backups/noust",
        total_size: 3 * 1024 ** 2,
        total_size_human: "3.00 MB",
        backup_count: 0,
        domains: [],
        misplaced: [],
        filesystem_total: 500 * 1024 ** 3,
        filesystem_free: 120 * 1024 ** 3,
      }),
    "GET /api/backup-schedules": () => json(200, { schedules: [], total: 0, default_retention_count: 10 }),
    "GET /api/backup-destinations": () => json(200, { destinations: [{ name: "nas", type: "sftp", encrypted: true }], total: 1 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0 }),
    ...extra,
  };
}
