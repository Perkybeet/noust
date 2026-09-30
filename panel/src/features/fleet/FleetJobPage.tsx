import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { RotateCcw } from "lucide-react";
import { useState } from "react";

import { ElevationCancelledError } from "../../api/client";
import { DetailPage } from "../../components/page/DetailPage";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatDuration, parseTimestamp } from "../../lib/format";
import { describeActor } from "../activity/data";
import { PlanView } from "./BulkActionDialog";
import {
  ACTION_WORDS,
  NODE_JOB_VIEW,
  batchOf,
  SKIP_REASONS,
  fleetJobQuery,
  fleetKeys,
  isJobRunning,
  isKnownAction,
  jobStatusView,
  nodeErrorOf,
  nodeJobState,
  retryFleetJob,
  retryable,
  textOf,
} from "./data";
import type { FleetJob, FleetNodeState, NodeJobState } from "./data";
import { HrefLink } from "./links";

/** The order a job's counts are said in: what went wrong first. */
const COUNT_ORDER: readonly NodeJobState[] = ["failed", "unreachable", "refused", "interrupted", "running", "queued", "succeeded", "skipped", "cancelled"];

function reasonWords(t: T, node: FleetNodeState): string | null {
  if (node.reason === null || node.reason === undefined) return null;
  const key = SKIP_REASONS[node.reason];
  return key !== undefined ? t(key) : node.reason;
}

function took(node: FleetNodeState): string | null {
  const start = parseTimestamp(node.started_at);
  const end = parseTimestamp(node.ended_at);
  if (start === null || end === null) return null;
  return formatDuration(Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000)));
}

function columns(t: T, canary: unknown, onOpen: (node: FleetNodeState) => void): Column<FleetNodeState>[] {
  return [
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "title",
      cell: (node) => (
        <HrefLink href={node.href} className="rounded-chip font-medium text-fg hover:underline hover:underline-offset-2" aria-label={t("fleet.jobs.nodeActivity", { name: node.node })}>
          <Mono>{node.node}</Mono>
        </HrefLink>
      ),
    },
    {
      id: "state",
      header: t("fleet.column.state"),
      card: "status",
      width: "w-44",
      cell: (node) => {
        const view = NODE_JOB_VIEW[nodeJobState(node.state)];
        const reason = reasonWords(t, node);
        return (
          <span className="flex min-w-0 flex-col items-start">
            <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
            {reason !== null ? <span className="text-12 text-fg-muted">{reason}</span> : null}
          </span>
        );
      },
    },
    {
      id: "step",
      header: t("fleet.column.step"),
      card: "meta",
      cell: (node) =>
        node.step !== null && node.step !== undefined && node.step !== "" ? (
          <Mono tone="muted" truncate title={node.step}>
            {node.step}
          </Mono>
        ) : (
          <EmptyCell reason={t("fleet.jobs.noStep")} />
        ),
    },
    {
      id: "batch",
      header: t("fleet.column.batch"),
      hideBelow: "md",
      align: "end",
      card: "hidden",
      cell: (node) => {
        const batch = batchOf(node, canary);
        return <span className="text-13 text-fg tabular-nums">{batch.canary ? t("fleet.jobs.canary") : String(batch.number)}</span>;
      },
    },
    {
      id: "time",
      header: t("fleet.column.took"),
      hideBelow: "md",
      align: "end",
      card: "meta",
      cell: (node) => {
        const duration = took(node);
        return duration === null ? <EmptyCell reason={t("fleet.jobs.notFinished")} /> : <span className="text-13 text-fg tabular-nums">{duration}</span>;
      },
    },
    {
      id: "details",
      header: t("fleet.column.details"),
      align: "end",
      card: "meta",
      cell: (node) => (
        <Button
          size="sm"
          variant="ghost"
          aria-label={t("fleet.jobs.detailsFor", { name: node.node })}
          onClick={() => {
            onOpen(node);
          }}
        >
          {t("fleet.jobs.details")}
        </Button>
      ),
    },
  ];
}

/** One server's part of the job: what happened there, the central's words and the node's own. */
function NodeDrawer({ t, node, onClose }: { t: T; node: FleetNodeState; onClose: () => void }) {
  const view = NODE_JOB_VIEW[nodeJobState(node.state)];
  const error = nodeErrorOf(node);
  const items = node.items ?? [];
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("fleet.jobs.drawerTitle", { name: node.node })}
      description={t(view.label)}
    >
      <div className="flex flex-col gap-5">
        <KeyValueList
          items={[
            { label: t("fleet.column.state"), value: <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />, copy: false, mono: false },
            ...(reasonWords(t, node) !== null ? [{ label: t("fleet.jobs.why"), value: reasonWords(t, node), copy: false as const, mono: false }] : []),
            { label: t("fleet.column.step"), value: node.step ?? null },
            { label: t("fleet.jobs.started"), value: node.started_at !== null && node.started_at !== undefined ? <RelativeTime value={node.started_at} /> : null, copy: false, mono: false },
            ...(node.node_jobs !== undefined && node.node_jobs.length > 0 ? [{ label: t("fleet.jobs.nodeJobs"), value: node.node_jobs.join(", ") }] : []),
          ]}
          empty={t("fleet.jobs.notYet")}
        />
        {error.message !== null ? (
          <Notice tone="error" title={error.message}>
            {error.hint}
          </Notice>
        ) : null}
        {node.output !== null && node.output !== undefined && node.output !== "" ? (
          <div className="flex flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("fleet.jobs.output", { name: node.node })}</p>
            <SystemOutput label={t("fleet.jobs.output", { name: node.node })} maxHeight="max-h-96">
              {node.output}
            </SystemOutput>
          </div>
        ) : null}
        {items.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("fleet.jobs.items")}</p>
            <ul className="flex flex-col divide-y divide-border">
              {items.map((item, index) => {
                const name = textOf(item["domain"]) ?? textOf(item["name"]) ?? String(index + 1);
                const state = nodeJobState(textOf(item["state"]) ?? "failed");
                const itemView = NODE_JOB_VIEW[state];
                const message = textOf(item["message"]);
                const output = textOf(item["output"]);
                return (
                  <li key={`${name}-${String(index)}`} className="flex min-w-0 flex-col gap-1 py-2">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className={cx("inline-flex items-center gap-1 text-13", stateTextClass(itemView.state))}>
                        <StatusGlyph state={itemView.state} size={10} />
                        <span className="text-fg">{t(itemView.label)}</span>
                      </span>
                      <Mono truncate>{name}</Mono>
                    </span>
                    {message !== null ? <span className="text-13 text-fg-muted">{message}</span> : null}
                    {output !== null ? (
                      <SystemOutput label={t("fleet.jobs.output", { name })} maxHeight="max-h-40">
                        {output}
                      </SystemOutput>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}

function Counts({ t, job }: { t: T; job: FleetJob }) {
  const parts = COUNT_ORDER.flatMap((state) => {
    const count = job.summary[state] ?? 0;
    return count > 0 ? [{ state, count }] : [];
  });
  return (
    <ul aria-label={t("fleet.jobs.countsLabel")} className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {parts.map(({ state, count }) => {
        const view = NODE_JOB_VIEW[state];
        return (
          <li key={state} className="inline-flex items-center gap-1.5 text-13">
            <StatusGlyph state={view.state} size={12} className={stateTextClass(view.state)} />
            <span className="text-fg">{t(`fleet.jobs.count.${state}`, { count })}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** The retry's plan, in a dialog, then the new job. */
function RetryDialog({ t, job, onClose }: { t: T; job: FleetJob; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // Not under the jobs' key: starting the retry refreshes the jobs, and must not plan it again.
  const plan = useQuery({ queryKey: ["fleet", "retry-plan", job.job_id], queryFn: () => retryFleetJob(job.job_id, true), staleTime: 0, gcTime: 0 });
  const run = useMutation({
    mutationFn: () => retryFleetJob(job.job_id, false),
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: fleetKeys.jobs });
      if (answer.job === null || answer.job === undefined) return;
      onClose();
      void navigate({ to: "/fleet/jobs/$id", params: { id: answer.job.job_id } });
    },
  });
  const action = isKnownAction(job.action) ? job.action : null;
  const runCount = plan.data?.plan.summary["run"] ?? 0;
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !run.isPending) onClose();
      }}
      size="lg"
      title={t("fleet.jobs.retry.title")}
      description={t("fleet.jobs.retry.description")}
      footer={
        <>
          <Button disabled={run.isPending} onClick={onClose}>
            {t("fleet.labels.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={run.isPending}
            disabled={plan.data === undefined || runCount === 0}
            onClick={() => {
              run.mutate();
            }}
          >
            {t("fleet.bulk.run.button", { count: runCount })}
          </Button>
        </>
      }
    >
      {plan.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <span className="sr-only">{t("fleet.bulk.plan.loading")}</span>
          <Skeleton className="h-4 w-80 max-w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : plan.isError ? (
        <ErrorBlock live error={plan.error} title={t("fleet.bulk.plan.failed")} onRetry={() => void plan.refetch()} />
      ) : action !== null ? (
        <div className="flex flex-col gap-4">
          <PlanView t={t} plan={plan.data.plan} action={action} />
          {run.isError && !(run.error instanceof ElevationCancelledError) ? <ErrorBlock live error={run.error} title={t("fleet.jobs.retry.failed")} /> : null}
        </div>
      ) : null}
    </Dialog>
  );
}

/**
 * One bulk action as it runs (T2): its state and counts at the top, then every server with its
 * state, its current step in the node's own words, and its output verbatim on request; "Retry
 * the failed ones" runs it again on the servers that did not get done, after its plan.
 */
export function FleetJobPage({ id }: { id: string }) {
  const t = useT();
  const job = useQuery(fleetJobQuery(id));
  const [open, setOpen] = useState<FleetNodeState | null>(null);
  const [retrying, setRetrying] = useState(false);

  const data = job.data;
  const actionLabel = data === undefined ? t("fleet.jobs.title") : isKnownAction(data.action) ? t(ACTION_WORDS[data.action].label) : data.title;
  const status = data === undefined ? null : jobStatusView(data.status);
  const again = data === undefined ? 0 : retryable(data);
  const total = data === undefined ? 0 : Object.values(data.summary).reduce((sum, count) => sum + count, 0);

  return (
    <DetailPage
      header={{
        title: actionLabel,
        breadcrumbs: [{ label: t("fleet.page.title"), to: "/fleet" }],
        ...(status !== null ? { status: <StatusPill state={status.state} label={t(status.label)} /> } : {}),
        ...(data !== undefined
          ? {
              meta: (
                <>
                  <span>{t("fleet.jobs.servers", { count: total })}</span>
                  <span aria-hidden="true">·</span>
                  <span>{t.rich("fleet.jobs.startedBy", { when: <RelativeTime key="when" value={data.created_at} />, who: data.created_by === null || data.created_by === undefined ? t("fleet.jobs.someone") : describeActor(t, data.created_by).label })}</span>
                  {data.retry_of !== null && data.retry_of !== undefined ? (
                    <>
                      <span aria-hidden="true">·</span>
                      <span>{t.rich("fleet.jobs.retryOf", { job: <Mono key="job">{data.retry_of}</Mono> })}</span>
                    </>
                  ) : null}
                </>
              ),
            }
          : {}),
        ...(again > 0
          ? {
              primaryAction: (
                <Button
                  variant="primary"
                  icon={<RotateCcw aria-hidden="true" />}
                  onClick={() => {
                    setRetrying(true);
                  }}
                >
                  {t("fleet.jobs.retry.button", { count: again })}
                </Button>
              ),
            }
          : {}),
      }}
    >
      {job.isError && data === undefined ? (
        <ErrorBlock error={job.error} title={t("fleet.jobs.loadFailed")} onRetry={() => void job.refetch()} retrying={job.isRefetching} />
      ) : (
        <div className="flex min-w-0 flex-col gap-4">
          {data?.status === "interrupted" ? <Notice tone="warning">{t("fleet.jobs.interrupted")}</Notice> : null}
          {data !== undefined ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Counts t={t} job={data} />
              <p className="text-12 text-fg-muted">{isJobRunning(data) ? t("fleet.jobs.refreshing") : t("fleet.jobs.finished")}</p>
            </div>
          ) : null}
          <DataTable
            caption={t("fleet.jobs.caption")}
            columns={columns(t, data?.request["canary"] ?? null, setOpen)}
            rows={data?.nodes ?? []}
            getRowId={(node) => node.node}
            loading={job.isPending}
            skeletonRows={3}
            mobile="cards"
          />
        </div>
      )}
      {open !== null ? (
        <NodeDrawer
          t={t}
          node={data?.nodes?.find((node) => node.node === open.node) ?? open}
          onClose={() => {
            setOpen(null);
          }}
        />
      ) : null}
      {retrying && data !== undefined ? (
        <RetryDialog
          t={t}
          job={data}
          onClose={() => {
            setRetrying(false);
          }}
        />
      ) : null}
    </DetailPage>
  );
}
