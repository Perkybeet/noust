/**
 * What feeds the task centre's store (item 114), mounted once in the shell: the sources are read here and
 * written into `store.ts`, and nothing else writes to it. A server's active jobs, its history, the operations
 * that run and, on "All servers", a central's view of its fleet are TanStack Query reads (so they retry, poll and
 * are invalidated like every read); the `job` and `operation` events are the fast path that moves a task between
 * them; and a task that ends is told to the person here (`notices.ts`), because this is the one place that has
 * both the event and the page they are on.
 *
 * It runs inside the selected server's scope (`NodeScope` remounts it on a switch), so what it hears is filed
 * under that server, and on "All servers" under the central's own, whose row the fleet view names `local`.
 */

import { useQueries, useQuery } from "@tanstack/react-query";
import { useNavigate, useRouter, useRouterState } from "@tanstack/react-router";
import { useEffect, useMemo, useRef } from "react";

import { sessionQuery, sessionsQuery } from "../../api/queries/auth";
import { activeJobsQuery, jobQuery, jobsQuery } from "../../api/queries/jobs";
import { fleetActiveQuery, operationsActiveQuery } from "../../api/queries/operations";
import { useNavScope } from "../../app/useNavScope";
import { useNode } from "../../nodes/useNode";
import { useServerEvent } from "../../realtime/events";
import { shouldOffer } from "./browserNotice";
import { endedTask, endingOf, isLeavingLongTask, noticeStateOf } from "./endings";
import { markJobEndHandled } from "../../realtime/jobEnds";
import { taskKey } from "./model";
import type { Me, Place, ServerKey } from "./model";
import { offerNotifications, sayEnding } from "./notices";
import { readOperationEvent } from "./model";
import { taskStore, useTaskState, withActiveJobs, withFleetItems, withJob, withJobs, withOperationEvent, withOperations } from "./store";

/**
 * How long before an answer arrived a read is taken to have begun. TanStack Query does not say when a request
 * went out, and the store only needs to know what the console heard of after that: a little too wide keeps a task,
 * never loses one.
 */
export const READ_SLACK_MS = 1_500;

/** How many of the newest jobs of the history the panel's "ended lately" is made from. */
const RECENT_JOBS = 15;

const NO_ME: Me = { username: null, session: null };

/**
 * Who the console is signed in as, in the labels a task's actor can carry: an account's name, or, for the master
 * token (no account), the first characters of this browser's own session, which is how its jobs are labelled.
 */
export function useMe(): Me {
  const { data: session } = useQuery(sessionQuery());
  const username = session?.account?.username ?? null;
  const needsSession = session?.authenticated === true && username === null;
  const sessions = useQuery({ ...sessionsQuery(), enabled: needsSession, staleTime: Infinity, retry: false });
  const current = sessions.data?.current_session ?? null;
  return useMemo(() => (username === null && current === null ? NO_ME : { username, session: current === null ? null : current.slice(0, 12) }), [username, current]);
}

/** Moves the console to a place of a task: what a toast's "See" and a notification's click do. */
export function useGo(): (place: Place) => void {
  const navigate = useNavigate();
  return (place) => {
    void navigate({ to: place.to as never, ...(place.params !== undefined ? { params: place.params as never } : {}), ...(place.search !== undefined ? { search: place.search as never } : {}) });
  };
}

/** The server a pinned key names, and the job id when it is a job's. */
function pinnedJobOf(key: string, node: ServerKey): string | null {
  const bar = key.indexOf("|");
  if (bar < 0 || (key.slice(0, bar) || null) !== node) return null;
  const rest = key.slice(bar + 1);
  return rest.startsWith("job:") ? rest.slice("job:".length) : null;
}

function useFeeds(): void {
  const { node } = useNode();
  const scope = useNavScope();
  const router = useRouter();
  const go = useGo();
  const me = useMe();
  const meRef = useRef(me);
  const goRef = useRef(go);
  useEffect(() => {
    meRef.current = me;
    goRef.current = go;
  });

  const server = scope === "server";
  const active = useQuery({ ...activeJobsQuery(), enabled: server, retry: false });
  const recent = useQuery({ ...jobsQuery({ limit: RECENT_JOBS }), enabled: server, retry: false });
  const operations = useQuery({ ...operationsActiveQuery(), enabled: server });
  const fleet = useQuery({ ...fleetActiveQuery(), enabled: scope === "fleet" });

  useEffect(() => {
    if (active.data === undefined) return;
    const { jobs } = active.data;
    const at = active.dataUpdatedAt;
    taskStore.update((state) => withActiveJobs(state, jobs, node, at, at - READ_SLACK_MS));
  }, [active.data, active.dataUpdatedAt, node]);

  useEffect(() => {
    if (recent.data === undefined) return;
    const { jobs } = recent.data;
    const at = recent.dataUpdatedAt;
    taskStore.update((state) => withJobs(state, jobs, node, at, at - READ_SLACK_MS));
  }, [recent.data, recent.dataUpdatedAt, node]);

  useEffect(() => {
    if (operations.data === undefined) return;
    const list = operations.data.operations;
    const at = operations.dataUpdatedAt;
    taskStore.update((state) => withOperations(state, list, node, at, at - READ_SLACK_MS));
  }, [operations.data, operations.dataUpdatedAt, node]);

  useEffect(() => {
    if (fleet.data === undefined) return;
    const { items, nodes } = fleet.data;
    const at = fleet.dataUpdatedAt;
    taskStore.update((state) => withFleetItems(state, items, nodes, at, at - READ_SLACK_MS));
  }, [fleet.data, fleet.dataUpdatedAt]);

  // A job that ended is told to the person here: the event is what moves it, and the page on screen decides.
  useServerEvent("job", (job) => {
    const key = taskKey(node, "job", job.id);
    const before = taskStore.get().tasks.get(key);
    taskStore.update((state) => withJob(state, job, node, Date.now()));
    const ended = endedTask(before, taskStore.get().tasks.get(key));
    if (ended === null) return;
    const ending = endingOf(ended, meRef.current, router.state.location.pathname);
    if (ending === "none") return;
    // The server's own notice for it follows in the same frame: this is the channel that says it.
    markJobEndHandled(noticeStateOf(ended));
    if (ending === "toast") sayEnding(ended, (place) => { goRef.current(place); });
  });

  useServerEvent("operation", (data) => {
    const event = readOperationEvent(data);
    if (event !== null) taskStore.update((state) => withOperationEvent(state, event, node, Date.now()));
  });

  // The first time the person leaves a long task of theirs, the browser's notification is offered, once.
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const was = useRef(pathname);
  useEffect(() => {
    if (was.current === pathname) return;
    was.current = pathname;
    if (!shouldOffer()) return;
    if (isLeavingLongTask(taskStore.get().tasks.values(), meRef.current, pathname, Date.now())) offerNotifications();
  }, [pathname]);
}

/**
 * A pinned job the store does not hold (the page was reloaded and it is older than the history the panel reads) is
 * read once, so its chip is there. What is held is never read again here: the events keep it.
 */
function usePinnedJobs(): void {
  const { node } = useNode();
  const state = useTaskState();
  const missing = useMemo(() => state.pinned.filter((key) => !state.tasks.has(key)).flatMap((key) => pinnedJobOf(key, node) ?? []), [state.pinned, state.tasks, node]);
  const reads = useQueries({ queries: missing.map((id) => ({ ...jobQuery(id), retry: false })) });
  useEffect(() => {
    const at = Date.now();
    const found = reads.flatMap((read) => (read.data === undefined ? [] : [read.data]));
    if (found.length > 0) taskStore.update((current) => found.reduce((next, job) => withJob(next, job, node, at), current));
  }, [reads, node]);
}

/** The task centre's feeds, mounted once in the shell. It draws nothing, so what changes in the store re-renders only this. */
export function TaskFeeds(): null {
  useFeeds();
  usePinnedJobs();
  return null;
}
