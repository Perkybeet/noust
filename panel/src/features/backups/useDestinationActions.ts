/**
 * Actions on backup destinations: one mutation per API call, matching `useBackupActions.ts`.
 * Creating, changing and removing a destination are sudo mode (the API client's "Confirm
 * it's you" flow handles that without any special-casing here); testing and browsing are not.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { backupDestinationKeys } from "../../api/queries/backupDestinations";
import { jobKeys } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { toast } from "../../components/ui/toast";
import { reportActionError } from "../apps/useAppActions";

export interface SaveDestinationInput {
  name: string;
  fields: Record<string, string>;
  encrypted: boolean;
}

export interface RestoreFromDestinationInput {
  destination: string;
  backupId: string;
  appName?: string | undefined;
  targetDomain?: string | undefined;
  restoreEnv: boolean;
}

export function useDestinationActions() {
  const queryClient = useQueryClient();

  const refreshList = (): void => {
    void queryClient.invalidateQueries({ queryKey: backupDestinationKeys.list });
  };

  const create = useMutation({
    mutationFn: (input: SaveDestinationInput & { backend: string }) =>
      request("post", "/api/backup-destinations", {
        body: { name: input.name, backend: input.backend, fields: input.fields, encrypted: input.encrypted },
      }),
    onSuccess: (result) => {
      toast.success(result.message);
      refreshList();
    },
  });

  const update = useMutation({
    mutationFn: (input: SaveDestinationInput) =>
      request("put", "/api/backup-destinations/{name}", {
        params: { name: input.name },
        body: { fields: input.fields, encrypted: input.encrypted },
      }),
    onSuccess: (result) => {
      toast.success(result.message);
      refreshList();
    },
  });

  const remove = useMutation({
    mutationFn: ({ name, force = false }: { name: string; force?: boolean }) =>
      request("delete", "/api/backup-destinations/{name}", { params: { name }, query: { force } }),
    onSuccess: (result) => {
      toast.success(result.message);
      refreshList();
    },
    onError: (error) => {
      reportActionError("Could not remove the destination", error);
    },
  });

  const test = useMutation({
    mutationFn: (name: string) => request("post", "/api/backup-destinations/{name}/test", { params: { name } }),
  });

  const showKey = useMutation({
    mutationFn: (name: string) => request("post", "/api/backup-destinations/{name}/show-key", { params: { name } }),
  });

  const restoreFromDestination = useMutation({
    mutationFn: (input: RestoreFromDestinationInput) =>
      request("post", "/api/backup-destinations/{name}/backups/{backup_id}/restore", {
        params: { name: input.destination, backup_id: input.backupId },
        body: { app_name: input.appName ?? null, target_domain: input.targetDomain ?? null, restore_env: input.restoreEnv },
      }),
    onSuccess: (result) => {
      queryClient.setQueryData<Job>(jobKeys.detail(result.job_id), (current) => current ?? (result.job as Job));
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      toast.info(result.message, { description: "You will be told when the restore finishes." });
    },
    onError: (error) => {
      reportActionError("Could not queue the restore", error);
    },
  });

  return { create, update, remove, test, showKey, restoreFromDestination };
}
