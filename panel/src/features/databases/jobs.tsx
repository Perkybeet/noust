/**
 * The job in hand on one database: the dump, restore, drop, check, upload or rotation the
 * operator just queued, shown once in the page's job slot (DetailPage `job`) whichever tab
 * started it, followed to its end, with its output verbatim a click away. A job on the same
 * database started elsewhere (the command line, another tab, a timer) shows there too while it
 * runs, so nobody restores over a dump that is still being taken.
 *
 * When a job ends, everything the page reads about the database is read again.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { databaseKeys } from "../../api/queries/databases";
import { activeJobsQuery, isJobFinished, jobLogQuery, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
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

/** What a job on a database does, which names it while it runs and once it ends. */
export type DatabaseJobKind =
  | "backup"
  | "restore"
  | "restoreNew"
  | "drop"
  | "verify"
  | "testRestore"
  | "push"
  | "rotate"
  | "link"
  | "working";

/** The 202 a queued job answers with: its id and, usually, a snapshot of it. */
export interface Accepted {
  job_id: string;
  job?: unknown;
}

interface JobWords {
  running: string;
  succeeded: string;
  failed: string;
}

/** One literal key per case, so tsc checks each one. */
export function databaseJobWords(t: T, kind: DatabaseJobKind, name: string): JobWords {
  switch (kind) {
    case "backup":
      return {
        running: t("databases.job.backup.running", { name }),
        succeeded: t("databases.job.backup.succeeded", { name }),
        failed: t("databases.job.backup.failed", { name }),
      };
    case "restore":
      return {
        running: t("databases.job.restore.running", { name }),
        succeeded: t("databases.job.restore.succeeded", { name }),
        failed: t("databases.job.restore.failed", { name }),
      };
    case "restoreNew":
      return {
        running: t("databases.job.restoreNew.running", { name }),
        succeeded: t("databases.job.restoreNew.succeeded", { name }),
        failed: t("databases.job.restoreNew.failed", { name }),
      };
    case "drop":
      return {
        running: t("databases.job.drop.running", { name }),
        succeeded: t("databases.job.drop.succeeded", { name }),
        failed: t("databases.job.drop.failed", { name }),
      };
    case "verify":
      return {
        running: t("databases.job.verify.running"),
        succeeded: t("databases.job.verify.succeeded"),
        failed: t("databases.job.verify.failed"),
      };
    case "testRestore":
      return {
        running: t("databases.job.testRestore.running"),
        succeeded: t("databases.job.testRestore.succeeded"),
        failed: t("databases.job.testRestore.failed"),
      };
    case "push":
      return {
        running: t("databases.job.push.running", { name }),
        succeeded: t("databases.job.push.succeeded", { name }),
        failed: t("databases.job.push.failed", { name }),
      };
    case "rotate":
      return {
        running: t("databases.job.rotate.running", { name }),
        succeeded: t("databases.job.rotate.succeeded", { name }),
        failed: t("databases.job.rotate.failed", { name }),
      };
    case "link":
      return {
        running: t("databases.job.link.running", { name }),
        succeeded: t("databases.job.link.succeeded", { name }),
        failed: t("databases.job.link.failed", { name }),
      };
    case "working":
      return {
        running: t("databases.job.working.running", { name }),
        succeeded: t("databases.job.working.succeeded", { name }),
        failed: t("databases.job.working.failed", { name }),
      };
  }
}

/** The kind of a job queued elsewhere, from the backend's job type. */
function kindOfType(type: string): DatabaseJobKind {
  if (type === "backup") return "backup";
  if (type === "restore") return "restore";
  return "working";
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

/** Whether a job is about one database, as its metadata names it. */
function isAbout(job: Job, engine: string, name: string): boolean {
  return job.metadata?.["engine"] === engine && job.metadata["database"] === name;
}

const RUNNING = new Set(["pending", "running"]);

export interface DatabaseJobApi {
  /**
   * Follows a job this page queued, and names what it does. `label` is the database the
   * words name when it is not this one (a restore into a new database).
   */
  track: (accepted: Accepted, kind: DatabaseJobKind, label?: string) => void;
  /** A job on this database is queued or running, here or anywhere else. */
  busy: boolean;
}

const NO_JOBS: DatabaseJobApi = { track: () => undefined, busy: false };
const DatabaseJobContext = createContext<DatabaseJobApi>(NO_JOBS);

interface Slot {
  job: Job | null;
  kind: DatabaseJobKind | null;
  label: string;
  mine: boolean;
  dismiss: () => void;
}

const SlotContext = createContext<Slot>({ job: null, kind: null, label: "", mine: false, dismiss: () => undefined });

/** The job API of a database page; outside a provider (a component alone in a test) it does nothing. */
export function useDatabaseJob(): DatabaseJobApi {
  return useContext(DatabaseJobContext);
}

export interface DatabaseJobProviderProps {
  engine: string;
  name: string;
  /** Called once when a drop this page queued has succeeded: the page has nothing left to show. */
  onDropped?: () => void;
  children: ReactNode;
}

/** Holds a database page's job in hand for every tab under it. */
export function DatabaseJobProvider({ engine, name, onDropped, children }: DatabaseJobProviderProps) {
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const [tracked, setTracked] = useState<{ id: string; kind: DatabaseJobKind; label: string } | null>(null);
  const active = useQuery({ ...activeJobsQuery(), refetchInterval: 10_000 });

  const elsewhere = active.data?.jobs.find((job) => isAbout(job, engine, name) && RUNNING.has(job.status)) ?? null;
  const mine = followed.job;
  const shown = mine ?? elsewhere;
  const kind = mine !== null ? (tracked?.kind ?? kindOfType(mine.type)) : elsewhere !== null ? kindOfType(elsewhere.type) : null;
  const label = mine !== null && tracked !== null ? tracked.label : name;
  const busy = (mine !== null && RUNNING.has(mine.status)) || elsewhere !== null;

  // What a finished job changed is read again once, when it ends.
  const settled = useRef<string | null>(null);
  const finished = shown !== null && isJobFinished(shown) ? shown : null;
  useEffect(() => {
    if (finished === null || settled.current === finished.id) return;
    settled.current = finished.id;
    void queryClient.invalidateQueries({ queryKey: databaseKeys.database(engine, name) });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.allBackups });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.policies });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
    if (finished.status === "completed" && tracked?.id === finished.id && tracked.kind === "drop") onDropped?.();
  }, [finished, queryClient, engine, name, tracked, onDropped]);

  const { follow, dismiss: stopFollowing } = followed;
  const track = useCallback(
    (accepted: Accepted, jobKind: DatabaseJobKind, jobLabel?: string) => {
      setTracked({ id: accepted.job_id, kind: jobKind, label: jobLabel ?? name });
      const snapshot = accepted.job as Job | undefined;
      follow(snapshot !== undefined && typeof snapshot.id === "string" ? snapshot : accepted.job_id);
    },
    [follow, name],
  );
  const dismiss = useCallback(() => {
    setTracked(null);
    stopFollowing();
  }, [stopFollowing]);

  const api = useMemo<DatabaseJobApi>(() => ({ track, busy }), [track, busy]);
  const slot = useMemo<Slot>(() => ({ job: shown, kind, label, mine: mine !== null, dismiss }), [shown, kind, label, mine, dismiss]);
  return (
    <DatabaseJobContext value={api}>
      <SlotContext value={slot}>{children}</SlotContext>
    </DatabaseJobContext>
  );
}

/** Whether the page's job slot has something to show: the layout leaves the slot out otherwise. */
export function useHasDatabaseJob(): boolean {
  return useContext(SlotContext).job !== null;
}

/** A job's whole output: its log while it runs, the file the job manager kept once it ended. */
export function JobOutputDrawer({ job, open, onOpenChange, title }: { job: Job; open: boolean; onOpenChange: (open: boolean) => void; title: string }) {
  const t = useT();
  const finished = isJobFinished(job);
  const log = useQuery({ ...jobLogQuery(job.id, 5000), enabled: open && finished });
  const lines = useMemo<LogLine[]>(() => {
    if (!finished) return jobLines(job);
    const content = log.data?.content ?? "";
    return content === "" ? jobLines(job) : content.split("\n").map((text, id) => ({ id, text }));
  }, [finished, job, log.data]);
  return (
    <Drawer open={open} onOpenChange={onOpenChange} size="lg" title={title} description={t("databases.job.outputDescription")}>
      {finished && log.isError && log.data === undefined ? (
        <ErrorBlock compact error={log.error} title={t("databases.job.outputFailed")} onRetry={() => void log.refetch()} />
      ) : finished && log.isPending ? (
        <div aria-busy="true">
          <span className="sr-only">{t("databases.job.outputLoading")}</span>
          <Skeleton className="h-80 w-full rounded-card" />
        </div>
      ) : (
        <LogViewer lines={lines} label={t("databases.job.outputLabel", { title })} filename={`${job.id}.log`} height={560} emptyMessage={t("databases.job.outputEmpty")} />
      )}
    </Drawer>
  );
}

/**
 * One job, the way the databases area shows it wherever it was started: waiting or running with
 * its step verbatim, done, or failed with the system's words; its output a click away.
 */
export function DatabaseJobProgress({ job, words, onDismiss }: { job: Job; words: JobWords; onDismiss?: (() => void) | undefined }) {
  const t = useT();
  const [outputOpen, setOutputOpen] = useState(false);
  const state = jobState(job.status);
  const title = state === "succeeded" ? words.succeeded : state === "failed" ? words.failed : words.running;
  const viewOutput = (
    <Button size="sm" variant="ghost" onClick={() => setOutputOpen(true)}>
      {t("databases.job.viewOutput")}
    </Button>
  );
  return (
    <>
      {state === "failed" ? (
        // A failure is an error block, which carries no action of its own: the output that
        // explains it sits right under it.
        <div className="flex min-w-0 flex-col items-start gap-1">
          <JobProgress
            state="failed"
            title={title}
            error={{ detail: job.error ?? jobStep(job) ?? "" }}
            {...(onDismiss !== undefined ? { onDismiss } : {})}
            className="w-full"
          />
          {viewOutput}
        </div>
      ) : (
        <JobProgress
          state={state}
          title={title}
          step={jobStep(job)}
          action={viewOutput}
          {...(onDismiss !== undefined && isJobFinished(job) ? { onDismiss } : {})}
        />
      )}
      <JobOutputDrawer job={job} open={outputOpen} onOpenChange={setOutputOpen} title={words.running} />
    </>
  );
}

/** The job slot's content: the job in hand, its step verbatim, and its output. */
export function DatabaseJobSlot() {
  const t = useT();
  const { job, kind, label, mine, dismiss } = useContext(SlotContext);
  if (job === null) return null;
  const words = databaseJobWords(t, kind ?? "working", label);
  return <DatabaseJobProgress job={job} words={words} {...(mine ? { onDismiss: dismiss } : {})} />;
}
