/**
 * The job in hand on the Server area: the update, cleanup or security change the operator
 * just queued, shown once in the page's job slot (DetailPage `job`) whichever tab started it,
 * followed to its end, with its output verbatim a click away. A server job started elsewhere
 * (the command line, another tab) shows there too while it runs, so nobody queues a second
 * update on top of one already running.
 *
 * When a job ends, everything the area reads is read again: an update changes the pending
 * list, a cleanup the disks, a security change the checks and the pending changes.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { activeJobsQuery, isJobFinished, jobLogQuery, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { serviceKeys } from "../../api/queries/services";
import { JobProgress } from "../../components/page/JobProgress";
import type { JobState } from "../../components/page/JobProgress";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Drawer } from "../../components/ui/Drawer";
import { LogViewer } from "../../components/ui/LogViewer";
import type { LogLine } from "../../components/ui/LogViewer";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { serverKeys } from "./queries";

/** What a job of this area does, which names it while it runs and once it ends. */
export type ServerJobKind = "update" | "refresh" | "repair" | "auto" | "cleanup" | "analyze" | "swap" | "security" | "checks" | "restarts";

/** The backend's job types that belong to the Server area (web/api/server). */
const KIND_BY_TYPE: Readonly<Record<string, ServerJobKind>> = {
  os_update: "update",
  os_refresh: "refresh",
  cleanup: "cleanup",
  disk_scan: "analyze",
  swap: "swap",
  server_action: "auto",
  server_security: "security",
};

export function jobKindOf(type: string): ServerJobKind | null {
  return KIND_BY_TYPE[type] ?? null;
}

interface JobWords {
  running: string;
  succeeded: string;
  failed: string;
}

/** One literal key per case, so tsc checks each one. */
export function jobWords(t: T, kind: ServerJobKind): JobWords {
  switch (kind) {
    case "update":
      return { running: t("server.job.update.running"), succeeded: t("server.job.update.succeeded"), failed: t("server.job.update.failed") };
    case "refresh":
      return { running: t("server.job.refresh.running"), succeeded: t("server.job.refresh.succeeded"), failed: t("server.job.refresh.failed") };
    case "repair":
      return { running: t("server.job.repair.running"), succeeded: t("server.job.repair.succeeded"), failed: t("server.job.repair.failed") };
    case "auto":
      return { running: t("server.job.auto.running"), succeeded: t("server.job.auto.succeeded"), failed: t("server.job.auto.failed") };
    case "cleanup":
      return { running: t("server.job.cleanup.running"), succeeded: t("server.job.cleanup.succeeded"), failed: t("server.job.cleanup.failed") };
    case "analyze":
      return { running: t("server.job.analyze.running"), succeeded: t("server.job.analyze.succeeded"), failed: t("server.job.analyze.failed") };
    case "swap":
      return { running: t("server.job.swap.running"), succeeded: t("server.job.swap.succeeded"), failed: t("server.job.swap.failed") };
    case "security":
      return { running: t("server.job.security.running"), succeeded: t("server.job.security.succeeded"), failed: t("server.job.security.failed") };
    case "checks":
      return { running: t("server.job.checks.running"), succeeded: t("server.job.checks.succeeded"), failed: t("server.job.checks.failed") };
    case "restarts":
      return { running: t("server.job.restarts.running"), succeeded: t("server.job.restarts.succeeded"), failed: t("server.job.restarts.failed") };
  }
}

/** The backend's job status in JobProgress's words. */
export function jobState(status: string): JobState {
  switch (status) {
    case "running":
      return "running";
    case "completed":
      return "succeeded";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    default:
      return "queued";
  }
}

/** The newest line the job logged, verbatim: what it is doing now. */
export function jobStep(job: Job): string | null {
  const message = job.logs?.at(-1)?.["message"];
  return typeof message === "string" && message.trim() !== "" ? message.trim() : null;
}

/** Every line a job logged so far, oldest first, verbatim. */
export function jobLines(job: Job): LogLine[] {
  return (job.logs ?? []).flatMap((entry, id) => {
    const message = entry["message"];
    return typeof message === "string" ? [{ id, text: message }] : [];
  });
}

interface Tracked {
  id: string;
  kind: ServerJobKind;
}

export interface ServerJobApi {
  /** Follows a job this area queued: the 202's `job_id`, and what it does. */
  track: (jobId: string, kind: ServerJobKind) => void;
  /** A job of this area is queued or running, here or anywhere else. */
  busy: boolean;
}

const NO_JOBS: ServerJobApi = { track: () => undefined, busy: false };

const ServerJobContext = createContext<ServerJobApi>(NO_JOBS);
const ServerJobSlotContext = createContext<{ job: Job | null; kind: ServerJobKind | null; mine: boolean; dismiss: () => void }>({
  job: null,
  kind: null,
  mine: false,
  dismiss: () => undefined,
});

/** The job API of the Server area; outside a provider (a component alone in a test) it does nothing. */
export function useServerJob(): ServerJobApi {
  return useContext(ServerJobContext);
}

const RUNNING = new Set(["pending", "running"]);

/** Holds the Server area's job in hand for every tab under it. */
export function ServerJobProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const [tracked, setTracked] = useState<Tracked | null>(null);
  const active = useQuery(activeJobsQuery());

  const elsewhere = active.data?.jobs.find((job) => jobKindOf(job.type) !== null && RUNNING.has(job.status)) ?? null;
  const mine = followed.job;
  const shown = mine ?? elsewhere;
  const kind = mine !== null ? (tracked?.kind ?? jobKindOf(mine.type)) : elsewhere !== null ? jobKindOf(elsewhere.type) : null;
  const busy = (mine !== null && RUNNING.has(mine.status)) || elsewhere !== null;

  // What a finished job changed is read again once, when it ends.
  const settled = useRef<string | null>(null);
  const finishedId = shown !== null && isJobFinished(shown) ? shown.id : null;
  useEffect(() => {
    if (finishedId === null || settled.current === finishedId) return;
    settled.current = finishedId;
    void queryClient.invalidateQueries({ queryKey: serverKeys.all });
    void queryClient.invalidateQueries({ queryKey: serviceKeys.all });
  }, [finishedId, queryClient]);

  const { follow, dismiss: stopFollowing } = followed;
  const track = useCallback(
    (jobId: string, jobKind: ServerJobKind) => {
      setTracked({ id: jobId, kind: jobKind });
      follow(jobId);
    },
    [follow],
  );
  const dismiss = useCallback(() => {
    setTracked(null);
    stopFollowing();
  }, [stopFollowing]);

  const api = useMemo<ServerJobApi>(() => ({ track, busy }), [track, busy]);
  const slot = useMemo(() => ({ job: shown, kind, mine: mine !== null, dismiss }), [shown, kind, mine, dismiss]);
  return (
    <ServerJobContext value={api}>
      <ServerJobSlotContext value={slot}>{children}</ServerJobSlotContext>
    </ServerJobContext>
  );
}

/** Whether the page's job slot has something to show: the layout leaves the slot out otherwise. */
export function useHasServerJob(): boolean {
  return useContext(ServerJobSlotContext).job !== null;
}

/** A job's whole output: its log while it runs, the file the job manager kept once it ended. */
function JobOutputDrawer({ job, open, onOpenChange, title }: { job: Job; open: boolean; onOpenChange: (open: boolean) => void; title: string }) {
  const t = useT();
  const finished = isJobFinished(job);
  const log = useQuery({ ...jobLogQuery(job.id, 5000), enabled: open && finished });
  const lines = useMemo<LogLine[]>(() => {
    if (!finished) return jobLines(job);
    const content = log.data?.content ?? "";
    return content === "" ? jobLines(job) : content.split("\n").map((text, id) => ({ id, text }));
  }, [finished, job, log.data]);
  return (
    <Drawer open={open} onOpenChange={onOpenChange} size="lg" title={title} description={t("server.job.outputDescription")}>
      {finished && log.isError && log.data === undefined ? (
        <ErrorBlock compact error={log.error} title={t("server.job.outputFailed")} onRetry={() => void log.refetch()} />
      ) : finished && log.isPending ? (
        <div aria-busy="true">
          <span className="sr-only">{t("server.job.outputLoading")}</span>
          <Skeleton className="h-80 w-full rounded-card" />
        </div>
      ) : (
        <LogViewer lines={lines} label={t("server.job.outputLabel", { title })} filename={`${job.id}.log`} height={560} emptyMessage={t("server.job.outputEmpty")} />
      )}
    </Drawer>
  );
}

/** The job slot's content: the job in hand, its step verbatim, and its output. */
export function ServerJobSlot() {
  const t = useT();
  const { job, kind, mine, dismiss } = useContext(ServerJobSlotContext);
  const [outputOpen, setOutputOpen] = useState(false);
  if (job === null) return null;
  const words = kind !== null ? jobWords(t, kind) : { running: job.name, succeeded: job.name, failed: job.name };
  const state = jobState(job.status);
  const title = state === "succeeded" ? words.succeeded : state === "failed" ? words.failed : words.running;
  const viewOutput = (
    <Button size="sm" variant="ghost" onClick={() => setOutputOpen(true)}>
      {t("server.job.viewOutput")}
    </Button>
  );
  return (
    <>
      {state === "failed" ? (
        <JobProgress
          state="failed"
          title={title}
          error={{ detail: job.error ?? jobStep(job) ?? "" }}
          action={viewOutput}
          {...(mine ? { onDismiss: dismiss } : {})}
        />
      ) : (
        <JobProgress
          state={state}
          title={title}
          step={jobStep(job)}
          action={viewOutput}
          {...(mine && isJobFinished(job) ? { onDismiss: dismiss } : {})}
        />
      )}
      <JobOutputDrawer job={job} open={outputOpen} onOpenChange={setOutputOpen} title={words.running} />
    </>
  );
}
