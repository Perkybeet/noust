import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { GitCommitHorizontal, RotateCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../../api/client";
import { appKeys, appQuery } from "../../../api/queries/apps";
import { deploymentKeys, deploymentsQuery } from "../../../api/queries/deployments";
import type { Deployment } from "../../../api/queries/deployments";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { MenuItem } from "../../../components/ui/Menu";
import { Notice } from "../../../components/ui/Notice";
import { SplitButton } from "../../../components/ui/SplitButton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { useRollback, useTrackJob } from "../RollbackHost";
import { shortCommit } from "./words";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";

const RUNNING = new Set(["queued", "running"]);

/** What a deployment's page can queue. Going back is the page's one rollback question, followed by the header. */
export type DeploymentActionKind = "rebuild";

function actionWords(t: T, domain: string): { queued: string; failed: string } {
  return { queued: t("appPages.deployments.actions.rebuildQueued", { domain }), failed: t("appPages.deployments.actions.rebuildFailed") };
}

/**
 * Follows the job a deployment's action queued (useFollowedJob: the job's events, and a poll
 * as the guarantee) and, when the job records a deploy of its own - found by its `job_id`,
 * never by timing - opens it, so the operator lands on its build log. A job that ends without
 * one (a release activated in seconds) is said to have finished, and one that fails keeps its
 * error on screen, in its own words.
 */
export function useDeploymentAction(domain: string, t: T) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const [kind, setKind] = useState<DeploymentActionKind | null>(null);
  const job = followed.job;
  const waiting = followed.id !== null && (job === null || !isJobFinished(job));
  const deployments = useQuery({
    ...deploymentsQuery({ domain, limit: 10 }),
    refetchInterval: waiting ? 1_000 : false,
  });
  const arrived = followed.id !== null ? deployments.data?.items.find((item) => item.job_id === followed.id) : undefined;
  const finished = job !== null && isJobFinished(job);
  const failed = finished && job.status !== "completed" ? job : null;
  const done = finished && job.status === "completed" && arrived === undefined ? job : null;

  useEffect(() => {
    if (arrived !== undefined) void navigate({ to: "/apps/$domain/deployments/$id", params: { domain, id: String(arrived.id) } });
  }, [arrived, domain, navigate]);

  // Once, when the job has ended: what it changed (the release serving, the history) is read again.
  const settledRef = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settledRef.current === job.id) return;
    settledRef.current = job.id;
    void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
    void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain) });
  }, [job, domain, queryClient]);

  return {
    kind,
    busy: waiting || (arrived === undefined && followed.id !== null && !finished),
    failed,
    done,
    follow: (next: DeploymentActionKind, queued: Job) => {
      setKind(next);
      announce(actionWords(t, domain).queued);
      followed.follow(queued);
    },
    dismiss: () => {
      followed.dismiss();
      setKind(null);
    },
  };
}

/** What rebuilding a deployment's commit does, on the app's layout. */
export function rebuildWords(t: T, layout: string, commit: string, branch: string | null): string {
  if (layout === "releases") return t("appPages.deployments.actions.rebuildReleasesWords", { commit });
  return branch !== null
    ? t("appPages.deployments.actions.rebuildInPlaceWordsWithBranch", { commit, branch })
    : t("appPages.deployments.actions.rebuildInPlaceWordsNoBranch", { commit });
}

/**
 * Builds a deployment's commit again (`POST .../rebuild`): the same call for "Retry" on a failed
 * deployment and "Rebuild this commit" on one that served, with or without the build cache. The
 * header follows the job like an update's, and `onQueued` lets the caller follow it too.
 */
export function useRebuild(domain: string, onQueued: (job: Job) => void) {
  const trackJob = useTrackJob();
  return useMutation({
    mutationFn: ({ id, noCache = false }: { id: number; noCache?: boolean }) =>
      request("post", "/api/apps/{domain}/deployments/{deployment_id}/rebuild", { params: { domain, deployment_id: id }, body: { no_cache: noCache } }),
    onSuccess: (accepted) => {
      const job = accepted.job as unknown as Job;
      trackJob(job);
      onQueued(job);
    },
  });
}

export function RebuildDialog({
  domain,
  deployment,
  layout,
  open,
  onOpenChange,
  onQueued,
  t,
}: {
  domain: string;
  deployment: Pick<Deployment, "id" | "git_commit" | "git_branch">;
  layout: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onQueued: (job: Job) => void;
  t: T;
}) {
  const commit = shortCommit(deployment.git_commit) ?? "";
  const rebuild = useRebuild(domain, (job) => {
    onOpenChange(false);
    onQueued(job);
  });
  const close = (next: boolean): void => {
    if (!next && rebuild.isPending) return;
    if (!next) rebuild.reset();
    onOpenChange(next);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={t("appPages.deployments.actions.rebuildDialogTitle", { commit })}
      description={rebuildWords(t, layout, commit, deployment.git_branch ?? null)}
      footer={
        <>
          <Button disabled={rebuild.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button variant="primary" loading={rebuild.isPending} onClick={() => rebuild.mutate({ id: deployment.id })}>
            {t("appPages.deployments.actions.rebuild")}
          </Button>
        </>
      }
    >
      {rebuild.isError ? <ErrorBlock live compact error={rebuild.error} title={t("appPages.deployments.actions.rebuildNotStarted")} /> : null}
    </Dialog>
  );
}

export interface DeploymentActionParts {
  /** What can be done about this deployment, for the page's title row: retry it, or go back to it and rebuild its commit. */
  buttons: ReactNode;
  /** How the action it started went: its failure in the job's words, or that it finished. */
  outcome: ReactNode;
  /** The dialogs the buttons open. */
  dialogs: ReactNode;
}

/**
 * What can be done from one deploy's page. A deployment that failed is tried again where the
 * error is: "Retry" builds its commit again, "Retry without cache" does it from scratch, and the
 * page follows the new job. One that served can be gone back to when the backend says it can
 * (`rollback_available`, through the page's one rollback question) and its commit built again.
 * Update in the header stays the page's one primary action, so these are all secondary.
 */
export function useDeploymentActions(domain: string, deployment: Deployment): DeploymentActionParts {
  const t = useT();
  const app = useQuery(appQuery(domain));
  const action = useDeploymentAction(domain, t);
  const { openRollback } = useRollback();
  const [dialog, setDialog] = useState<"rebuild" | null>(null);
  const retry = useRebuild(domain, (job) => {
    action.follow("rebuild", job);
  });
  const running = RUNNING.has(deployment.status);
  const layout = app.data?.layout ?? null;
  const commit = shortCommit(deployment.git_commit);
  const available = deployment.rollback_available;
  // A deploy an operator cancelled is stored as failed, and nothing went wrong in it.
  const failed = deployment.status === "failed" && !deployment.cancelled;

  if (running || layout === null) return { buttons: null, outcome: null, dialogs: null };

  const buttons = failed ? (
    commit !== null ? (
      <SplitButton
        name={t("appPages.deployments.actions.retry")}
        icon={<RotateCw aria-hidden="true" />}
        loading={retry.isPending || (action.busy && action.kind === "rebuild")}
        onClick={() => {
          action.dismiss();
          retry.mutate({ id: deployment.id }, { onError: (error) => reportActionError(t("appPages.deployments.actions.retryFailed"), error) });
        }}
        menu={
          <MenuItem
            icon={<VERB_ICONS.rebuild />}
            onClick={() => {
              action.dismiss();
              retry.mutate({ id: deployment.id, noCache: true }, { onError: (error) => reportActionError(t("appPages.deployments.actions.retryFailed"), error) });
            }}
          >
            {t("appPages.deployments.actions.retryFresh")}
          </MenuItem>
        }
      >
        {t("appPages.deployments.actions.retry")}
      </SplitButton>
    ) : null
  ) : (
    <>
      {available ? (
        <Button
          icon={<OBJECT_ICONS.rollback aria-hidden="true" />}
          disabled={action.busy}
          onClick={() => {
            action.dismiss();
            openRollback({ deployment: deployment.id });
          }}
        >
          {t("appPages.deployments.actions.goBackToThis")}
        </Button>
      ) : null}
      {commit !== null ? (
        <Button
          icon={<GitCommitHorizontal aria-hidden="true" />}
          disabled={action.busy}
          loading={action.busy && action.kind === "rebuild"}
          onClick={() => {
            action.dismiss();
            setDialog("rebuild");
          }}
        >
          {t("appPages.deployments.actions.rebuildThisCommit")}
        </Button>
      ) : null}
    </>
  );

  const outcome =
    action.failed !== null && action.kind !== null ? (
      <ErrorBlock
        live
        compact
        error={{ detail: action.failed.error ?? t("appPages.job.noReason") }}
        title={actionWords(t, domain).failed}
      />
    ) : action.done !== null && action.kind !== null ? (
      <Notice tone="success" live>
        {t("appPages.deployments.actions.rebuiltCommit", { commit: commit ?? "" })}
      </Notice>
    ) : null;

  const dialogs =
    commit !== null && !failed ? (
      <RebuildDialog
        domain={domain}
        deployment={deployment}
        layout={layout}
        open={dialog === "rebuild"}
        onOpenChange={(open) => setDialog(open ? "rebuild" : null)}
        onQueued={(job) => action.follow("rebuild", job)}
        t={t}
      />
    ) : null;

  return { buttons, outcome, dialogs };
}
