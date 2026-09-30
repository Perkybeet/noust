import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../api/client";
import type { ApiError } from "../../api/errors";
import { appKeys } from "../../api/queries/apps";
import { jobKeys } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { announce } from "../../app/Announcer";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { describeError } from "../../lib/errors";
import { reportHeld } from "../../lib/held";

export interface AppActionOptions {
  /**
   * Called with a job the API queued (update, rollback). The app page tracks it in
   * its header; without one, a toast says the job was queued.
   */
  onJobQueued?: (job: Job) => void;
}

/**
 * Reports a failed action in the toast queue: what failed, the fix, the system's words. A
 * cancelled "Confirm it's you" is not a failure, and is said as such.
 */
export function reportActionError(title: string, error: unknown): void {
  if (reportHeld(error)) return;
  const { hint, detail, output } = describeError(error);
  toast.error(title, { detail, ...(hint !== null ? { description: hint } : {}), ...(output !== null ? { output } : {}) });
}

/**
 * An update the API refused because the branch has nothing the live build lacks (`409
 * nothing_new`): not a failure, a question - rebuild the same commit anyway?
 */
export function isNothingNew(error: unknown): error is ApiError {
  return isApiError(error) && error.error === "nothing_new";
}

type UnitVerb = "restart" | "start" | "stop";

function unitSuccessToast(t: T, verb: UnitVerb, domain: string): string {
  switch (verb) {
    case "restart":
      return t("apps.actions.restartedToast", { domain });
    case "start":
      return t("apps.actions.startedToast", { domain });
    case "stop":
      return t("apps.actions.stoppedToast", { domain });
  }
}

function unitErrorToast(t: T, verb: UnitVerb, domain: string): string {
  switch (verb) {
    case "restart":
      return t("apps.actions.restartFailed", { domain });
    case "start":
      return t("apps.actions.startFailed", { domain });
    case "stop":
      return t("apps.actions.stopFailed", { domain });
  }
}

function callUnit(domain: string, verb: UnitVerb) {
  const params = { params: { domain } };
  switch (verb) {
    case "restart":
      return request("post", "/api/apps/{domain}/restart", params);
    case "start":
      return request("post", "/api/apps/{domain}/start", params);
    case "stop":
      return request("post", "/api/apps/{domain}/stop", params);
  }
}

/** One synchronous systemctl verb on the app's unit, reported in a toast either way. */
function useUnitAction(t: T, domain: string, verb: UnitVerb, refresh: () => void) {
  return useMutation({
    mutationFn: () => callUnit(domain, verb),
    onSuccess: () => {
      toast.success(unitSuccessToast(t, verb, domain));
      refresh();
    },
    onError: (error) => {
      reportActionError(unitErrorToast(t, verb, domain), error);
      refresh();
    },
  });
}

/**
 * The actions on one application, each the one API call that does it: restart, start and
 * stop the unit, queue an update or a rollback, switch releases. Elevation ("Confirm it's you")
 * is handled by the API client, never here.
 *
 * Synchronous unit actions refresh the app when they return (the `app` server event does too;
 * whichever lands first wins). Queued jobs report their outcome through the `job` and
 * `notice` events, which the shell already turns into a toast, so nothing here repeats it.
 */
export function useAppActions(domain: string, { onJobQueued }: AppActionOptions = {}) {
  const t = useT();
  const queryClient = useQueryClient();

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
  };

  const queued = (job: Job, kind: "update" | "rollback"): void => {
    // The job's own events keep this entry current, and can beat the response here: a job
    // that fails in milliseconds has already said so on the stream. The response's snapshot
    // only fills an empty entry, never overwrites a newer one.
    queryClient.setQueryData<Job>(jobKeys.detail(job.id), (current) => current ?? job);
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    const message = kind === "update" ? t("apps.actions.updateQueued", { domain }) : t("apps.actions.rollbackQueued", { domain });
    // Said once: the toast is itself announced (it lives in a live region); a caller that takes
    // the operator somewhere instead of toasting gets the sentence spoken here.
    if (onJobQueued) {
      announce(message);
      onJobQueued(job);
    } else {
      toast.info(message, { description: t("apps.actions.queuedDescription") });
    }
  };

  const restart = useUnitAction(t, domain, "restart", refresh);
  const start = useUnitAction(t, domain, "start", refresh);
  const stop = useUnitAction(t, domain, "stop", refresh);

  // The refusal that asks "rebuild anyway?", kept apart from the mutations' own errors so the
  // question stays open while the forced retry is in flight.
  const [nothingNew, setNothingNew] = useState<ApiError | null>(null);
  const callUpdate = (force: boolean) => request("post", "/api/jobs/update", { body: { domain, force } });
  const update = useMutation({
    mutationFn: () => callUpdate(false),
    onSuccess: (result) => {
      setNothingNew(null);
      queued(result.job, "update");
    },
    onError: (error) => {
      if (isNothingNew(error)) {
        setNothingNew(error);
        return;
      }
      reportActionError(t("apps.actions.updateCouldNotQueue", { domain }), error);
    },
  });
  // The same update, told to rebuild the commit that is live: only after nothing_new asked.
  const rebuildAnyway = useMutation({
    mutationFn: () => callUpdate(true),
    onSuccess: (result) => {
      setNothingNew(null);
      queued(result.job, "update");
    },
    onError: (error) => {
      setNothingNew(null);
      reportActionError(t("apps.actions.updateCouldNotQueue", { domain }), error);
    },
  });

  const rollbackToBackup = useMutation({
    mutationFn: (backupId: string) => request("post", "/api/jobs/rollback", { body: { domain, backup_id: backupId } }),
    onSuccess: (result) => {
      queued(result.job, "rollback");
    },
  });

  const activateRelease = useMutation({
    mutationFn: (releaseId: string) =>
      request("post", "/api/apps/{domain}/releases/{release_id}/activate", { params: { domain, release_id: releaseId } }),
    onSuccess: (result) => {
      refresh();
      // `rolled_back` says the release is older than the one it replaced. A release that fails
      // its health check never gets here: the API answers an error, after putting the
      // previous release back.
      if (!result.changed) toast.info(t("apps.actions.releaseAlreadyServing", { release: result.release_id, domain }));
      else if (result.rolled_back) toast.success(t("apps.actions.rolledBack", { domain, release: result.release_id }));
      else toast.success(t("apps.actions.releaseActivated", { release: result.release_id, domain }));
    },
  });

  // Deleting is not here: it goes through features/app/useDeleteApp, the endpoint behind sudo
  // mode (DELETE /api/apps/{domain}), never POST /api/jobs/delete, which does not ask.
  return {
    restart,
    start,
    stop,
    update,
    rebuildAnyway,
    /** The update's `nothing_new` refusal, while its question is open (see NothingNewDialog). */
    nothingNew,
    dismissNothingNew: () => {
      setNothingNew(null);
    },
    rollbackToBackup,
    activateRelease,
  };
}
