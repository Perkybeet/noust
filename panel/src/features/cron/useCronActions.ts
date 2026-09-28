import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { BodyOf } from "../../api/client";
import { cronKeys } from "../../api/queries/cron";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";

export type CreateCronJobBody = BodyOf<"/api/cron", "post">;

/**
 * Every action on a cron job: creating (or rewriting) one, running it now, enabling and
 * disabling its timer, and deleting it. All but create/delete answer immediately, from a
 * synchronous systemctl call.
 */
export function useCronActions() {
  const t = useT();
  const queryClient = useQueryClient();

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: cronKeys.all });
  };

  const create = useMutation({
    mutationFn: (body: CreateCronJobBody) => request("post", "/api/cron", { body }),
    onSuccess: (result) => {
      toast.success(
        t("cron.toast.created", { name: result.job?.name ?? t("cron.toast.theJob") }),
        result.job ? { description: t("cron.toast.nextRun", { value: result.job.next_run }) } : {},
      );
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (name: string) => request("delete", "/api/cron/{name}", { params: { name } }),
    onSuccess: (_result, name) => {
      toast.success(t("cron.toast.deleted", { name }));
      refresh();
    },
    onError: (error, name) => {
      reportActionError(t("cron.toast.deleteError", { name }), error);
    },
  });

  const run = useMutation({
    mutationFn: (name: string) => request("post", "/api/cron/{name}/run", { params: { name } }),
    onSuccess: (_result, name) => {
      toast.success(t("cron.toast.started", { name }), { description: t("cron.toast.startedDescription") });
    },
    onError: (error, name) => {
      reportActionError(t("cron.toast.startError", { name }), error);
    },
  });

  const enable = useMutation({
    mutationFn: (name: string) => request("post", "/api/cron/{name}/enable", { params: { name } }),
    onSuccess: (_result, name) => {
      toast.success(t("cron.toast.enabled", { name }));
      refresh();
    },
    onError: (error, name) => {
      reportActionError(t("cron.toast.enableError", { name }), error);
      refresh();
    },
  });

  const disable = useMutation({
    mutationFn: (name: string) => request("post", "/api/cron/{name}/disable", { params: { name } }),
    onSuccess: (_result, name) => {
      toast.success(t("cron.toast.disabled", { name }));
      refresh();
    },
    onError: (error, name) => {
      reportActionError(t("cron.toast.disableError", { name }), error);
      refresh();
    },
  });

  return { create, remove, run, enable, disable };
}
