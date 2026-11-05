import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Pin, PinOff } from "lucide-react";
import { useMemo } from "react";

import { isApiError, request } from "../../api/client";
import { isJobFinished, jobQuery } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { announce } from "../../app/Announcer";
import { DetailPage } from "../../components/page/DetailPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { useAnnounceChange } from "../../components/page/useAnnounceChange";
import { useNow } from "../../components/page/clock";
import { Button, buttonClassName } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { LogViewer } from "../../components/ui/LogViewer";
import type { LogLine } from "../../components/ui/LogViewer";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { formatDuration, parseTimestamp } from "../../lib/format";
import { useHasFleet } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { levelOf, logClockText, logSpan } from "../app/deployments/buildLog";
import { PhaseTimeline } from "../app/deployments/PhaseTimeline";
import { logClockOffset, outcomeOf, timeline } from "../app/deployments/phases";
import { jobEvents } from "../app/deployments/buildLog";
import { useJobRecord } from "../app/deployments/useDeploymentJob";
import { CancelJobButton } from "../queue/CancelJobButton";
import { useCancelJob } from "../queue/useCancelJob";
import { cancelReasonWords, cancelState } from "../queue/words";
import { useMe } from "./feeds";
import { taskKey, taskOfJob } from "./model";
import type { Task } from "./model";
import { endingWords } from "./notices";
import { pin, unpin, useTaskState } from "./store";
import { whoOf } from "./who";
import { stepOf, titleOf, viewOf } from "./words";

/** The jobs whose steps are the deploy's five phases, which the page draws as its line of steps. */
const DEPLOY_JOBS: ReadonlySet<string> = new Set(["deploy", "update", "rollback", "push", "zero_downtime", "migrate"]);

/** The jobs that can be asked again from here: the ones that put a new version of an application in front of people. */
const RETRYABLE: ReadonlySet<string> = new Set(["deploy", "update"]);

const REASON_ID = "task-cancel-reason";

function TaskSkeleton() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-4">
      <Skeleton className="h-28 w-full rounded-card" />
      <Skeleton className="h-64 w-full rounded-card" />
    </div>
  );
}

/** What the page says of a task that is not a deploy: where it is, and how far. */
function Doing({ task, title }: { task: Task; title: string }) {
  const t = useT();
  const step = stepOf(task, t.locale);
  return (
    <div className="flex flex-col gap-1">
      <p className="text-13 text-fg">{task.state === "queued" ? t.rich("tasks.page.waitingFor", { what: <Mono key="what">{step ?? ""}</Mono> }) : step !== null && step !== "" ? t("tasks.page.step", { step }) : title}</p>
      {task.progress !== null ? <p className="text-12 text-fg-muted tabular-nums">{task.progress} %</p> : null}
    </div>
  );
}

function TaskView({ job }: { job: Job }) {
  const t = useT();
  const { node } = useNode();
  const me = useMe();
  const hasFleet = useHasFleet();
  const navigate = useNavigate();
  const cancel = useCancelJob();
  const { pinned } = useTaskState();
  const finished = isJobFinished(job);
  const task = useMemo(() => taskOfJob(job, node, 0), [job, node]);
  const title = titleOf(task, t.locale);
  const view = viewOf(task.state, t.locale);
  const key = taskKey(node, "job", job.id);
  const isPinned = pinned.includes(key);
  const record = useJobRecord(job.id, !finished);
  const now = useNow(() => (finished ? 3_600_000 : 1_000));
  const domain = task.domain;

  const lines = useMemo<LogLine[]>(() => record.entries.map((entry, id) => {
        const level = levelOf(entry.text);
        return { id, text: entry.text, ...(level !== undefined ? { level } : {}), ...(entry.at ? { ts: logClockText(entry.at) } : {}) };
      }), [record.entries]);

  const deploys = DEPLOY_JOBS.has(job.type);
  const phases = useMemo(() => {
    if (!deploys) return null;
    const span = logSpan(record.entries.map((entry) => entry.at));
    return timeline(jobEvents(record.entries), outcomeOf(job.status), { lastAt: span.last, now: new Date(now), offset: logClockOffset(span.first, parseTimestamp(job.started_at)) });
  }, [deploys, record.entries, job.status, job.started_at, now]);

  // The state is said once each time it changes, and a failure interrupts.
  useAnnounceChange(task.state, finished ? endingWords(task, t.locale) : task.state === "queued" ? t("tasks.page.announceQueued", { title }) : t("tasks.page.announceRunning", { title }), task.state === "failed" ? "assertive" : "polite");

  const state = cancelState(job, undefined);
  const again = useMutation({
    mutationFn: () => request("post", "/api/jobs/update", { body: { domain: domain ?? "", force: false } }),
    onSuccess: (queued) => {
      announce(t("tasks.page.announceQueued", { title }));
      void navigate({ to: "/activity/tasks/$id", params: { id: queued.job.id }, ...(node !== null ? { search: { node } as never } : {}) });
    },
    onError: (error) => {
      reportActionError(t("tasks.page.tryAgainFailed", { title }), error);
    },
  });
  const canTryAgain = task.state === "failed" && RETRYABLE.has(job.type) && domain !== null;
  const began = parseTimestamp(job.started_at ?? job.created_at);
  const ended = parseTimestamp(job.completed_at);
  const took = finished && began !== null && ended !== null ? Math.max(0, (ended.getTime() - began.getTime()) / 1000) : null;

  return (
    <>
      <DetailPage
        header={{
          title,
          status: <StatusPill state={view.state} label={view.label} />,
          meta: (
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span>{whoOf(task, me, t)}</span>
              {hasFleet ? <Mono tone="muted">{node ?? ""}</Mono> : null}
              {began !== null ? <span>{t.rich("tasks.page.startedAt", { when: <RelativeTime key="when" value={began} /> })}</span> : null}
              {took !== null ? <span>{t("tasks.page.tookFor", { duration: formatDuration(took, t.locale) })}</span> : null}
            </span>
          ),
          secondaryActions: (
            <>
              {task.state === "running" || isPinned ? (
                <Button
                  icon={isPinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />}
                  aria-pressed={isPinned}
                  onClick={() => {
                    if (isPinned) unpin(key);
                    else pin(key);
                  }}
                >
                  {isPinned ? t("tasks.page.unpin") : t("tasks.page.pin")}
                </Button>
              ) : null}
              {!finished ? <CancelJobButton entry={job} state={state} onAsk={cancel.ask} reasonId={REASON_ID} /> : null}
              {task.state === "failed" && job.deployment_id !== null && job.deployment_id !== undefined && domain !== null ? (
                <Link to="/apps/$domain/deployments/$id" params={{ domain, id: String(job.deployment_id) }} className={buttonClassName("secondary")}>
                  {t("tasks.page.deployment")}
                </Link>
              ) : null}
            </>
          ),
          ...(canTryAgain
            ? {
                primaryAction: (
                  <Button variant="primary" icon={<VERB_ICONS.reset aria-hidden="true" />} loading={again.isPending} onClick={() => again.mutate()}>
                    {t("common.errorBlock.tryAgain")}
                  </Button>
                ),
              }
            : {}),
        }}
      >
        <div className="flex flex-col gap-6">
          {task.state === "failed" && job.error !== null && job.error !== undefined && job.error !== "" ? <ErrorBlock compact error={{ detail: job.error }} title={endingWords(task, t.locale)} /> : null}
          <Card as="section" level={2} title={t("tasks.page.steps")}>
            {phases !== null ? <PhaseTimeline phases={phases} outcome={outcomeOf(job.status)} /> : <Doing task={task} title={title} />}
            {state.kind === "cannot" ? (
              <p id={REASON_ID} className="mt-3 text-12 text-pretty text-fg-muted">
                {cancelReasonWords(state.reason, t.locale)}
              </p>
            ) : null}
          </Card>
          <Card
            as="section"
            level={2}
            title={t("tasks.page.log")}
            {...(!finished ? { description: t("tasks.page.live") } : {})}
            actions={<span className="text-12 text-fg-muted">{t("tasks.page.lines", { count: lines.length })}</span>}
          >
            <LogViewer lines={lines} label={t("tasks.page.logLabel", { title })} filename={`${job.id}.log`} height={360} loading={record.socket === "connecting" && lines.length === 0} emptyMessage={t("tasks.page.logEmpty")} follow={!finished} />
          </Card>
        </div>
      </DetailPage>
      {cancel.dialog}
    </>
  );
}

/** A task's own page (Activity > Tasks > one): its line of steps, who and where, its log as it is written, and what can be done about it. */
export function TaskPage({ id }: { id: string }) {
  const t = useT();
  const query = useQuery(jobQuery(id));
  if (query.data !== undefined) return <TaskView job={query.data} />;
  if (query.isError) {
    return (
      <DetailPage header={{ title: t("tasks.page.title", { id }), mono: true }}>
        {isApiError(query.error) && query.error.status === 404 ? (
          <EmptyState variant="firstUse" icon={<OBJECT_ICONS.work />} title={t("tasks.page.notFound")} description={t("tasks.page.notFoundHelp")} />
        ) : (
          <ErrorBlock error={query.error} title={t("tasks.page.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        )}
      </DetailPage>
    );
  }
  return (
    <DetailPage header={{ title: t("tasks.page.title", { id }), mono: true }}>
      <LoadingRegion label={t("tasks.page.loading")}>
        <TaskSkeleton />
      </LoadingRegion>
    </DetailPage>
  );
}
