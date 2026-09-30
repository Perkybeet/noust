import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { GitCommitHorizontal, History } from "lucide-react";
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
import { Notice } from "../../../components/ui/Notice";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RollbackDialog } from "../RollbackDialog";
import { shortCommit } from "./words";

const RUNNING = new Set(["queued", "running"]);

export type DeploymentActionKind = "rebuild" | "rollback";

function actionWords(t: T, kind: DeploymentActionKind, domain: string): { queued: string; failed: string } {
  return kind === "rebuild"
    ? { queued: t("appPages.deployments.actions.rebuildQueued", { domain }), failed: t("appPages.deployments.actions.rebuildFailed") }
    : { queued: t("appPages.deployments.actions.rollbackQueued", { domain }), failed: t("appPages.deployments.actions.rollbackFailed") };
}

/**
 * Follows the job a deployment's action queued (useFollowedJob: the job's events, and a poll
 * as the guarantee) and, when the job records a deploy of its own - found by its `job_id`,
 * never by timing - opens it, so the operator lands on its build log. A job that ends without
 * one (a release activated in seconds) is said to have finished, and one that fails keeps its
 * error on screen, in its own words.
 */
function useDeploymentAction(domain: string, t: T) {
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
      announce(actionWords(t, next, domain).queued);
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

/** What going back to a deployment does, on the app's layout. */
export function rollbackWords(t: T, layout: string, deployment: Deployment): string {
  const id = String(deployment.id);
  if (layout === "releases") return t("appPages.deployments.actions.rollbackReleasesWords", { id: deployment.release_id ?? "" });
  return t("appPages.deployments.actions.rollbackInPlaceWords", { snapshot: deployment.snapshot_backup ?? "", id });
}

function RebuildDialog({
  domain,
  deployment,
  layout,
  open,
  onOpenChange,
  onQueued,
  t,
}: {
  domain: string;
  deployment: Deployment;
  layout: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onQueued: (job: Job) => void;
  t: T;
}) {
  const commit = shortCommit(deployment.git_commit) ?? "";
  const rebuild = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/deployments/{deployment_id}/rebuild", { params: { domain, deployment_id: deployment.id } }),
    onSuccess: (accepted) => {
      onOpenChange(false);
      onQueued(accepted.job as unknown as Job);
    },
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
          <Button variant="primary" loading={rebuild.isPending} onClick={() => rebuild.mutate()}>
            {t("appPages.deployments.actions.rebuild")}
          </Button>
        </>
      }
    >
      {rebuild.isError ? <ErrorBlock live compact error={rebuild.error} title={t("appPages.deployments.actions.rebuildNotStarted")} /> : null}
    </Dialog>
  );
}

function RollbackToDeploymentDialog({
  domain,
  deployment,
  layout,
  open,
  onOpenChange,
  onQueued,
  onChooseAnother,
  t,
}: {
  domain: string;
  deployment: Deployment;
  layout: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onQueued: (job: Job) => void;
  onChooseAnother: () => void;
  t: T;
}) {
  const rollback = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/deployments/{deployment_id}/rollback", { params: { domain, deployment_id: deployment.id } }),
    onSuccess: (accepted) => {
      onOpenChange(false);
      onQueued(accepted.job as unknown as Job);
    },
  });
  const close = (next: boolean): void => {
    if (!next && rollback.isPending) return;
    if (!next) rollback.reset();
    onOpenChange(next);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={t("appPages.deployments.actions.rollbackDialogTitle", { id: String(deployment.id) })}
      description={rollbackWords(t, layout, deployment)}
      footer={
        <>
          <Button variant="ghost" disabled={rollback.isPending} onClick={onChooseAnother} className="sm:mr-auto">
            {t("appPages.deployments.actions.chooseAnotherVersion")}
          </Button>
          <Button disabled={rollback.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button variant="primary" loading={rollback.isPending} onClick={() => rollback.mutate()}>
            {t("appPages.deployments.actions.goBack")}
          </Button>
        </>
      }
    >
      {rollback.isError ? <ErrorBlock live compact error={rollback.error} title={t("appPages.rollback.notStarted")} /> : null}
    </Dialog>
  );
}

export interface DeploymentActionParts {
  /** Go back to this deploy (when the backend says it can) and rebuild its commit, for the page's title row. */
  buttons: ReactNode;
  /** How the action it started went: its failure in the job's words, or that it finished. */
  outcome: ReactNode;
  /** The dialogs the buttons open. */
  dialogs: ReactNode;
}

/**
 * What can be done from one deploy's page: go back to what it produced, only when that is
 * still possible (`rollback_available`, decided by the backend; otherwise nothing is offered
 * here, and the header's "Roll back…" lists what can be gone back to), and deploy its exact
 * commit again. The header's Update stays the page's one primary action, so both are
 * secondary here.
 */
export function useDeploymentActions(domain: string, deployment: Deployment): DeploymentActionParts {
  const t = useT();
  const app = useQuery(appQuery(domain));
  const action = useDeploymentAction(domain, t);
  const [dialog, setDialog] = useState<"rebuild" | "rollback" | "chooser" | null>(null);
  const running = RUNNING.has(deployment.status);
  const layout = app.data?.layout ?? null;
  const commit = shortCommit(deployment.git_commit);
  const available = deployment.rollback_available;

  const queued = (kind: DeploymentActionKind) => (job: Job) => {
    action.follow(kind, job);
  };
  const setOpen = (which: typeof dialog) => (open: boolean) => {
    setDialog(open ? which : null);
  };

  if (running || layout === null) return { buttons: null, outcome: null, dialogs: null };

  const buttons = (
    <>
      {available ? (
        <Button
          icon={<History aria-hidden="true" />}
          disabled={action.busy}
          loading={action.busy && action.kind === "rollback"}
          onClick={() => {
            action.dismiss();
            setDialog("rollback");
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
        title={actionWords(t, action.kind, domain).failed}
      />
    ) : action.done !== null && action.kind !== null ? (
      <Notice tone="success" live>
        {action.kind === "rollback"
          ? t("appPages.deployments.actions.rolledBackTo", { id: String(deployment.id) })
          : t("appPages.deployments.actions.rebuiltCommit", { commit: commit ?? "" })}
      </Notice>
    ) : null;

  const dialogs = (
    <>
      {commit !== null ? (
        <RebuildDialog
          domain={domain}
          deployment={deployment}
          layout={layout}
          open={dialog === "rebuild"}
          onOpenChange={setOpen("rebuild")}
          onQueued={queued("rebuild")}
          t={t}
        />
      ) : null}
      {available ? (
        <RollbackToDeploymentDialog
          domain={domain}
          deployment={deployment}
          layout={layout}
          open={dialog === "rollback"}
          onOpenChange={setOpen("rollback")}
          onQueued={queued("rollback")}
          onChooseAnother={() => setDialog("chooser")}
          t={t}
        />
      ) : null}
      <RollbackDialog domain={domain} layout={layout} open={dialog === "chooser"} onOpenChange={setOpen("chooser")} onJobQueued={queued("rollback")} />
    </>
  );

  return { buttons, outcome, dialogs };
}
