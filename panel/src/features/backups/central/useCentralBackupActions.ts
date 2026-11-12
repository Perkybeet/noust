/**
 * Actions on the copies a central holds: one mutation per API call. Changing the settings, deleting a copy and
 * giving one back are sudo mode (the API client's "Confirm it's you" flow handles that without any special-casing
 * here); asking for a pass is not. Giving a copy back is a job, followed in Activity.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { announce } from "../../../app/Announcer";
import { request } from "../../../api/client";
import { centralBackupKeys } from "../../../api/queries/centralBackups";
import type { CollectSettingsBody, GiveBackBody } from "../../../api/queries/centralBackups";
import { jobKeys } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";

export function useCentralBackupActions() {
  const t = useT();
  const queryClient = useQueryClient();

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: centralBackupKeys.all });
  };

  const saveSettings = useMutation({
    mutationFn: (body: CollectSettingsBody) => request("put", "/api/central-backups/settings", { body }),
    onSuccess: (status) => {
      queryClient.setQueryData(centralBackupKeys.status, status);
      refresh();
      announce(t("centralBackups.settings.saved"));
    },
    // No onError: the drawer shows a failure where it was started.
  });

  /** Turns the fleet's collecting on or off without opening the drawer: the one switch the destinations card has. */
  const setEnabled = useMutation({
    mutationFn: (enabled: boolean) => request("put", "/api/central-backups/settings", { body: { enabled } }),
    onSuccess: (status) => {
      queryClient.setQueryData(centralBackupKeys.status, status);
      refresh();
      announce(t("centralBackups.settings.saved"));
    },
    onError: (error) => {
      reportActionError(t("centralBackups.settings.saveFailed"), error);
    },
  });

  const follow = useMutation({
    mutationFn: ({ node, follow: answer }: { node: string; follow: boolean | null }) =>
      request("put", "/api/central-backups/nodes/{node}", { params: { node }, body: { follow: answer } }),
    onSuccess: (status) => {
      queryClient.setQueryData(centralBackupKeys.status, status);
      refresh();
    },
    onError: (error) => {
      reportActionError(t("centralBackups.settings.saveFailed"), error);
    },
  });

  const collect = useMutation({
    mutationFn: (node: string | null) => request("post", "/api/central-backups/collect", { body: { node, force: true } }),
    onSuccess: (status) => {
      queryClient.setQueryData(centralBackupKeys.status, status);
      announce(t("centralBackups.action.queued"));
      // What it takes shows up in the list as it arrives, and the status polls while a server is moving.
      window.setTimeout(refresh, 1500);
    },
    onError: (error) => {
      reportActionError(t("centralBackups.page.loadFailed"), error);
    },
  });

  const remove = useMutation({
    mutationFn: ({ node, kind, item }: { node: string; kind: string; item: string; owner: string }) =>
      request("delete", "/api/central-backups/{node}/{kind}", { params: { node, kind }, query: { item } }),
    onSuccess: (_result, input) => {
      refresh();
      announce(t("centralBackups.action.deleted", { owner: input.owner }));
    },
    // No onError: the confirmation shows a failure where it was started.
  });

  const giveBack = useMutation({
    mutationFn: ({ node, kind, body }: { node: string; kind: string; body: GiveBackBody; owner: string; server: string }) =>
      request("post", "/api/central-backups/{node}/{kind}/restore", { params: { node, kind }, body }),
    onSuccess: (result, input) => {
      queryClient.setQueryData<Job>(jobKeys.detail(result.job_id), (current) => current ?? (result.job as Job));
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      toast.info(t("centralBackups.give.queued", { owner: input.owner, server: input.server }), { description: t("centralBackups.give.queuedDescription") });
    },
    // No onError: the dialog shows a failure where it was started.
  });

  return { saveSettings, setEnabled, follow, collect, remove, giveBack };
}
