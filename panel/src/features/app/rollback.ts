/**
 * What an application can go back to, and what going back does, read off its deployments.
 *
 * The backend decides what can be gone back to (`rollback_available`, with its method): a release
 * on disk, the images a stack kept, a commit that is built again, a snapshot backup. The console
 * lists those and says the consequences from the same rows, so the question it asks is never a
 * sentence written for a layout the app is not on.
 */

import type { Deployment } from "../../api/queries/deployments";

export type RollbackMethod = "instant" | "rebuild" | "snapshot";

/** A deployment the app can go back to. */
export interface RollbackTarget {
  kind: "deployment";
  /** The deployment's id, the way the list and the API name it. */
  id: number;
  commit: string | null;
  message: string | null;
  /** When it was made. */
  when: string | null;
  method: RollbackMethod;
  /** In place, the backup that holds what it produced. */
  snapshot: string | null;
  /** Later deployments that changed the database schema: going back passes them. */
  schemaBetween: readonly number[];
}

/** A backup of an app kept in a single folder, restored as a job. */
export interface BackupTarget {
  kind: "backup";
  id: string;
  commit: string | null;
  when: string | null;
  description: string;
  sizeBytes: number;
}

export type Target = RollbackTarget | BackupTarget;

const SUCCEEDED = new Set(["success", "completed"]);

function methodOf(deployment: Deployment): RollbackMethod {
  const method = deployment.rollback_method;
  if (method === "rebuild" || method === "snapshot") return method;
  return "instant";
}

/** The deployments the app can go back to, newest first. */
export function rollbackTargets(deployments: readonly Deployment[]): RollbackTarget[] {
  return deployments
    .filter((deployment) => deployment.rollback_available)
    .map((deployment) => ({
      kind: "deployment" as const,
      id: deployment.id,
      commit: deployment.git_commit?.slice(0, 7) ?? null,
      message: deployment.commit_message ?? null,
      when: deployment.started_at ?? null,
      method: methodOf(deployment),
      snapshot: deployment.snapshot_backup ?? null,
      schemaBetween: deployment.schema_changed_between ?? [],
    }))
    .sort((a, b) => b.id - a.id);
}

/**
 * The version that kept serving while a deployment failed: the newest one before it that
 * succeeded. Null for the first deploy of an app, when nothing was serving.
 */
export function keptServing(deployments: readonly Deployment[], failed: Pick<Deployment, "id">): number | null {
  const before = deployments.filter((deployment) => deployment.id < failed.id && SUCCEEDED.has(deployment.status) && !deployment.cancelled);
  return before.reduce<number | null>((newest, deployment) => (newest === null || deployment.id > newest ? deployment.id : newest), null);
}

/**
 * The deployment that serves now: the newest one that succeeded and was not cancelled. A rollback
 * is recorded as a deployment of its own, so after one it is the version gone back to.
 */
export function servingDeployment(deployments: readonly Deployment[]): Deployment | null {
  return (
    [...deployments]
      .filter((deployment) => SUCCEEDED.has(deployment.status) && !deployment.cancelled)
      .sort((a, b) => b.id - a.id)[0] ?? null
  );
}
