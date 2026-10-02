/**
 * Going back past a change to the database's schema (spec 3.2, section 1.2). Every way back -
 * a rollback to a deployment, a release activated, a backup's files put back - is refused by
 * the backend with 409 `schema_changed` when later deployments changed the schema, and runs
 * only when asked again with `schema_changed_ok`. Noust puts code back, never a database: the
 * operator decides, having been told which deployments and which migrations.
 */

import { isApiError } from "../../api/client";
import type { Deployment } from "../../api/queries/deployments";
import type { RollbackPoint } from "../../api/queries/apps";
import { parseTimestamp } from "../../lib/format";

/** The refusal, as the console reads it. */
export interface SchemaChangeRefusal {
  /** The deployments that changed the schema, oldest first; empty when the answer did not name them. */
  deployments: readonly number[];
  /** The backend's own sentence, naming what is gone back past. */
  detail: string;
  /** The backend's fix (restore the backup, or confirm). */
  hint: string | null;
}

/** Reads a 409 refusal to go back past a schema change; null for any other error. */
export function schemaChangeRefusal(error: unknown): SchemaChangeRefusal | null {
  if (!isApiError(error) || error.status !== 409 || error.error !== "schema_changed") return null;
  const named = error.extra["deployments"];
  const deployments = Array.isArray(named) ? named.filter((id): id is number => typeof id === "number" && Number.isInteger(id)) : [];
  return { deployments: [...deployments].sort((a, b) => a - b), detail: error.detail, hint: error.hint };
}

/** What changed the schema in one deployment: its hooks marked `migrates` that succeeded, Prisma's automatic run included. */
export function migrationsOf(deployment: Pick<Deployment, "hooks">): NonNullable<Deployment["hooks"]> {
  return (deployment.hooks ?? []).filter((hook) => hook.migrates && hook.ok);
}

/**
 * The backup taken before the first deployment that changed the schema: the newest one made
 * before it started. Each update takes one first, so going back to it puts the database back
 * as it was before those migrations ran.
 */
export function backupBefore(points: readonly RollbackPoint[], firstChange: Pick<Deployment, "started_at"> | undefined): RollbackPoint | null {
  const started = parseTimestamp(firstChange?.started_at ?? null);
  if (started === null) return null;
  let best: RollbackPoint | null = null;
  let bestAt = -Infinity;
  for (const point of points) {
    const at = parseTimestamp(point.created_at)?.getTime();
    if (at === undefined || at > started.getTime() || at <= bestAt) continue;
    best = point;
    bestAt = at;
  }
  return best;
}

/**
 * The command that puts the database back from a backup. A Compose stack's backup holds its
 * databases, and `--databases-only` restores only them, leaving the code that was just put
 * back; any other app's backup is restored whole.
 */
export function restoreCommand(backupId: string, appType: string | null | undefined): string {
  return appType === "docker-compose" ? `noust backup restore ${backupId} --databases-only` : `noust backup restore ${backupId}`;
}
