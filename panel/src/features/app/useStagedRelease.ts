/**
 * Building a release without serving it, and making a release that waits serve
 * (`POST /api/apps/{domain}/releases/stage`, `POST .../releases/{id}/promote`). Both are jobs: the
 * first builds the branch into a release that waits, the second puts that very release live
 * behind the health gate and runs its migrations, with nothing built again.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { deploymentKeys } from "../../api/queries/deployments";
import { jobKeys } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { announce } from "../../app/Announcer";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";

/** What the job answers, kept as the app's actions keep theirs: the job's own events keep it current. */
function seedJob(queryClient: ReturnType<typeof useQueryClient>, job: Job): void {
  queryClient.setQueryData<Job>(jobKeys.detail(job.id), (current) => current ?? job);
  void queryClient.invalidateQueries({ queryKey: jobKeys.active });
}

/** Builds the app's branch into a release that waits; the header follows the job when it is given one. */
export function useStageRelease(domain: string, onJobQueued?: (job: Job) => void) {
  const t = useT();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/releases/stage", { params: { domain }, body: { no_cache: false } }),
    onSuccess: (result) => {
      const job = result.job as Job;
      seedJob(queryClient, job);
      const message = t("rollout.releases.buildOnlyQueued", { domain });
      if (onJobQueued) {
        announce(message);
        onJobQueued(job);
      } else {
        toast.info(message, { description: t("rollout.releases.buildOnlyHint") });
      }
      void queryClient.invalidateQueries({ queryKey: appKeys.releases(domain) });
    },
    onError: (error) => {
      reportActionError(t("rollout.releases.buildOnlyFailed", { domain }), error);
    },
  });
}

export interface PromoteArgs {
  id: string;
  /** Going past a deployment that changed the database schema was confirmed. */
  schemaChangedOk?: boolean;
  /** Going ahead although deploys are frozen was confirmed. */
  frozenOk?: boolean;
}

/**
 * Makes a release that waits the one that serves. Refusals the operator can decide on (a schema
 * change, a freeze) are left to the caller, which asks and tries again; the rest is reported.
 */
export function usePromoteRelease(domain: string) {
  const t = useT();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, schemaChangedOk = false, frozenOk = false }: PromoteArgs) =>
      request("post", "/api/apps/{domain}/releases/{release_id}/promote", {
        params: { domain, release_id: id },
        body: { schema_changed_ok: schemaChangedOk, frozen_ok: frozenOk },
      }),
    onSuccess: (result, { id }) => {
      seedJob(queryClient, result.job as Job);
      toast.info(t("rollout.releases.promoteQueued", { id, domain }));
      void queryClient.invalidateQueries({ queryKey: appKeys.releases(domain) });
      void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
    },
  });
}
