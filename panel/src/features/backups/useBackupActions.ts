/**
 * Actions on backups and their schedules: one mutation per API call, matching
 * `features/apps/useAppActions.ts`. Creating and restoring a backup are jobs (both tar or
 * untar a whole application tree); verifying, deleting and scheduling answer immediately.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ElevationCancelledError, request } from "../../api/client";
import { backupKeys } from "../../api/queries/backups";
import { jobKeys } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { describeError } from "../../lib/errors";
import { reportActionError } from "../apps/useAppActions";

export interface CreateBackupInput {
  domain: string;
  description: string;
  includeEnv: boolean;
  includeNodeModules: boolean;
  includeBuild: boolean;
  includeDatabase: boolean;
  includeDockerVolumes: boolean;
  redisMethod: string;
  tags: string[];
}

export interface RestoreBackupInput {
  backupId: string;
  targetDomain?: string | undefined;
  restoreEnv: boolean;
  verify: boolean;
  /** Going back past deployments that changed the database schema was confirmed (SchemaChangeDialog). */
  schemaChangedOk?: boolean;
}

export interface ScheduleDestinationInput {
  name: string;
  retentionCount?: number | undefined;
  retentionDays?: number | undefined;
}

export interface CreateScheduleInput {
  domain: string;
  schedule: string;
  /** Backups this schedule made to keep; null leaves `backup.max_per_app` in charge, as 2.1 did. */
  retentionCount: number | null;
  /** Maximum age in days of a backup this schedule made; null for no age limit. */
  retentionDays: number | null;
  includeDatabases: boolean;
  destinations: ScheduleDestinationInput[];
}

/** Some applications of a bulk schedule were refused: each one named, with the server's words. */
export class SchedulesFailed extends Error {
  readonly detail: string;

  constructor(lines: readonly string[]) {
    super(lines.join("\n"));
    this.name = "SchedulesFailed";
    this.detail = lines.join("\n");
  }
}

export interface PushBackupInput {
  backupId: string;
  destination: string;
}

export function useBackupActions() {
  const t = useT();
  const queryClient = useQueryClient();

  const refreshList = (): void => {
    void queryClient.invalidateQueries({ queryKey: backupKeys.all });
    void queryClient.invalidateQueries({ queryKey: backupKeys.storage });
  };

  const queueJob = (result: { job_id: string; job?: unknown; message: string }, description: string): void => {
    queryClient.setQueryData<Job>(jobKeys.detail(result.job_id), (current) => current ?? (result.job as Job));
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    toast.info(result.message, { description });
  };

  const create = useMutation({
    mutationFn: (input: CreateBackupInput) =>
      request("post", "/api/backups", {
        body: {
          domain: input.domain,
          description: input.description,
          include_env: input.includeEnv,
          include_node_modules: input.includeNodeModules,
          include_build: input.includeBuild,
          include_database: input.includeDatabase,
          include_docker_volumes: input.includeDockerVolumes,
          schemas: [],
          redis_method: input.redisMethod,
          tags: input.tags,
        },
      }),
    onSuccess: (result) => {
      queueJob(result, t("backups.toast.backupQueuedDescription"));
    },
    onError: (error, input) => {
      reportActionError(t("backups.toast.createError", { domain: input.domain }), error);
    },
  });

  const verify = useMutation({
    mutationFn: (backupId: string) => request("post", "/api/backups/{backup_id}/verify", { params: { backup_id: backupId } }),
    // The manager records last_verified_at and verified_ok against the backup itself; refetch
    // so the row reflects the server's own verdict instead of a client-only, session-lived one.
    onSuccess: refreshList,
  });

  const restore = useMutation({
    mutationFn: (input: RestoreBackupInput) =>
      request("post", "/api/backups/{backup_id}/restore", {
        params: { backup_id: input.backupId },
        body: {
          target_domain: input.targetDomain ?? null,
          restore_env: input.restoreEnv,
          verify: input.verify,
          schema_changed_ok: input.schemaChangedOk ?? false,
        },
      }),
    onSuccess: (result) => {
      queueJob(result, t("backups.toast.restoreQueuedDescription"));
    },
    // No onError: the restore dialog (ConfirmDialog) shows a failure where it was started.
  });

  const remove = useMutation({
    mutationFn: (backupId: string) => request("delete", "/api/backups/{backup_id}", { params: { backup_id: backupId } }),
    onSuccess: (result) => {
      toast.success(result.message);
      refreshList();
    },
    onError: (error) => {
      reportActionError(t("backups.toast.deleteError"), error);
    },
  });

  const push = useMutation({
    mutationFn: (input: PushBackupInput) =>
      request("post", "/api/backups/{backup_id}/push", {
        params: { backup_id: input.backupId },
        body: { destination: input.destination },
      }),
    onSuccess: (result) => {
      queueJob(result, t("backups.toast.copyQueuedDescription"));
    },
    onError: (error) => {
      reportActionError(t("backups.toast.copyQueueError"), error);
    },
  });

  const scheduleBody = (input: CreateScheduleInput) => ({
    domain: input.domain,
    schedule: input.schedule,
    retention_count: input.retentionCount,
    retention_days: input.retentionDays,
    include_databases: input.includeDatabases,
    destinations: input.destinations.map((destination) => ({
      name: destination.name,
      retention_count: destination.retentionCount ?? null,
      retention_days: destination.retentionDays ?? null,
    })),
  });

  const createSchedule = useMutation({
    mutationFn: (input: CreateScheduleInput) => request("post", "/api/backup-schedules", { body: scheduleBody(input) }),
    onSuccess: (result) => {
      toast.success(result.message);
      void queryClient.invalidateQueries({ queryKey: backupKeys.schedules });
    },
  });

  // Every application without a schedule, one request each: the API schedules one application
  // at a time. Those that fail are named with the server's own words, and the rest stand.
  const createSchedules = useMutation({
    mutationFn: async (inputs: CreateScheduleInput[]) => {
      const failed: string[] = [];
      for (const input of inputs) {
        try {
          await request("post", "/api/backup-schedules", { body: scheduleBody(input) });
        } catch (error: unknown) {
          if (error instanceof ElevationCancelledError) throw error;
          failed.push(`${input.domain}: ${describeError(error).detail}`);
        }
      }
      if (failed.length > 0) throw new SchedulesFailed(failed);
      return inputs.length;
    },
    onSuccess: (count) => {
      toast.success(t("backups.toast.scheduledMany", { count }));
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: backupKeys.schedules });
    },
  });

  const updateSchedule = useMutation({
    mutationFn: (input: CreateScheduleInput) =>
      request("put", "/api/backup-schedules/{domain}", { params: { domain: input.domain }, body: scheduleBody(input) }),
    onSuccess: (result) => {
      toast.success(result.message);
      void queryClient.invalidateQueries({ queryKey: backupKeys.schedules });
    },
  });

  const deleteSchedule = useMutation({
    mutationFn: (domain: string) => request("delete", "/api/backup-schedules/{domain}", { params: { domain } }),
    onSuccess: (result) => {
      toast.success(result.message);
      void queryClient.invalidateQueries({ queryKey: backupKeys.schedules });
    },
    onError: (error) => {
      reportActionError(t("backups.toast.removeScheduleError"), error);
    },
  });

  return { create, verify, restore, remove, push, createSchedule, createSchedules, updateSchedule, deleteSchedule };
}
