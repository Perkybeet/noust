/**
 * What a task is, for the console's task centre (item 114): one shape for the three things the
 * server reports as work in progress, whichever way each arrives.
 *
 * - A background **job** (a deploy, a backup, an update of the system): `GET /api/jobs/active`, the
 *   history and the `job` event, with its title as a sentence of the server's catalog.
 * - A long **operation** that answers on its own request (a restart, a reboot, a verification): the
 *   `operation` event and `GET /api/operations/active`. It has no progress and no log.
 * - What a **central** gets from each server of its fleet (`GET /api/fleet/jobs/active`): both of the
 *   above, per server, with how the server answered.
 *
 * Everything here is pure: the store (`store.ts`) holds the tasks and the feeds (`feeds.ts`) read the
 * sources, and neither decides what a task is. A task is told apart by a key that includes its server,
 * because two servers may hold the same job id.
 */

import type { Job } from "../../api/queries/jobs";
import type { Operation } from "../../api/queries/operations";
import type { CodedText } from "../../lib/serverSentence";
import { asCoded } from "../../lib/serverSentence";
import { parseTimestamp } from "../../lib/format";

export type TaskKind = "job" | "operation";

/** The five states of the queue, which an operation shares: it is running, or it ended one way. */
export type TaskState = "queued" | "running" | "succeeded" | "failed" | "cancelled";

/** The server a task runs on: null for the one the console is on, a node's name on a central. */
export type ServerKey = string | null;

export interface Task {
  /** Unique across servers and kinds: `<server>|job:<id>` (see {@link taskKey}). */
  key: string;
  /** The id the server gave it: a job's, or an operation's `op-...`. */
  id: string;
  kind: TaskKind;
  server: ServerKey;
  state: TaskState;
  /** What it is, as a sentence of the catalog; null for a job from a server that sends none. */
  title: CodedText | null;
  /** The English the server wrote beside the title (a job's `name`); empty for an operation. */
  name: string;
  /** The job's type (`deploy`, `backup`...); null for an operation. */
  type: string | null;
  /** Who asked, as the audit trail names them: an account, `master`, `token:<name>`, a session. */
  actor: string | null;
  /** The application it concerns, when it concerns one. */
  domain: string | null;
  /** An operation's subject (a service, a node): what its link opens. */
  target: string | null;
  /** The operation's sentence code, for where its link leads. */
  code: string | null;
  /** How far a job is, 0 to 100; null when it cannot say (an operation, a job with no steps). */
  progress: number | null;
  /** What it is doing now, in the server's English, with its sentence when it is one of Noust's. */
  step: string | null;
  stepCoded: CodedText | null;
  /** The instants the server reported, in milliseconds since the epoch; null when it has none. */
  createdAt: number | null;
  startedAt: number | null;
  completedAt: number | null;
  /** What a failed job reported, verbatim. */
  error: string | null;
  /** Where a queued job stands (1 starts next) and what it waits for, in the queue's own words. */
  queuePosition: number | null;
  waitingFor: string | null;
  /** Whether cancelling it would be accepted, as the queue said; null when the event did not say. */
  cancellable: boolean | null;
  /** Why a running job cannot be stopped, when the server said. */
  cancelBlockedReason: string | null;
  /** It was asked to stop and still runs. */
  cancelRequested: boolean;
  /** When the console last heard of it, on the browser's clock: what lets a snapshot be told from a newer event. */
  touched: number;
}

/** The key a task is held under: the server first, so a node's job and this server's never meet. */
export function taskKey(server: ServerKey, kind: TaskKind, id: string): string {
  return `${server ?? ""}|${kind}:${id}`;
}

export function isActive(task: Pick<Task, "state">): boolean {
  return task.state === "queued" || task.state === "running";
}

export function isFinished(task: Pick<Task, "state">): boolean {
  return !isActive(task);
}

const STATES: ReadonlySet<string> = new Set(["queued", "running", "succeeded", "failed", "cancelled"]);

/** The state of a job: its own `state` when the server sends one, else read from the status word an older server sends. */
export function stateOfJob(job: Pick<Job, "status"> & { state?: string }): TaskState {
  if (job.state !== undefined && STATES.has(job.state)) return job.state as TaskState;
  switch (job.status) {
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

function instant(value: string | null | undefined): number | null {
  return parseTimestamp(value)?.getTime() ?? null;
}

function percentOf(job: Pick<Job, "progress" | "total_steps">): number | null {
  return job.total_steps > 0 ? Math.min(100, Math.max(0, Math.round((job.progress / job.total_steps) * 100))) : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** A job as the API and the `job` event say it, as a task. */
export function taskOfJob(job: Job, server: ServerKey, now: number): Task {
  const state = stateOfJob(job);
  const domain = text(job.metadata?.["domain"]);
  const step = text(job.current_step);
  return {
    key: taskKey(server, "job", job.id),
    id: job.id,
    kind: "job",
    server,
    state,
    title: asCoded(job.title),
    name: job.name,
    type: job.type,
    actor: text(job.actor),
    domain,
    target: null,
    code: null,
    progress: state === "running" ? percentOf(job) : null,
    step,
    stepCoded: asCoded(job.current_step_coded),
    createdAt: instant(job.created_at),
    startedAt: instant(job.started_at),
    completedAt: instant(job.completed_at),
    error: text(job.error),
    queuePosition: typeof job.queue_position === "number" ? job.queue_position : null,
    waitingFor: text(job.waiting_for),
    cancellable: typeof job.cancellable === "boolean" ? job.cancellable : null,
    cancelBlockedReason: text(job.cancel_blocked_reason),
    cancelRequested: job.cancel_requested,
    touched: now,
  };
}

/** An operation that is running, from the list or from its `start` event. */
export function taskOfOperation(operation: Operation, server: ServerKey, now: number): Task {
  return {
    key: taskKey(server, "operation", operation.id),
    id: operation.id,
    kind: "operation",
    server,
    state: "running",
    title: asCoded(operation.title),
    name: "",
    type: null,
    actor: text(operation.actor),
    domain: text(operation.domain),
    target: text(operation.target),
    code: text(operation.title.code),
    progress: null,
    step: null,
    stepCoded: null,
    createdAt: instant(operation.started_at),
    startedAt: instant(operation.started_at),
    completedAt: null,
    error: null,
    queuePosition: null,
    waitingFor: null,
    cancellable: false,
    cancelBlockedReason: null,
    cancelRequested: false,
    touched: now,
  };
}

/** The body of the `operation` event: a `start` or an `end` with its result. */
export interface OperationEvent {
  id: string;
  phase: "start" | "end";
  title: CodedText;
  target: string | null;
  domain: string | null;
  actor: string | null;
  at: string;
  result?: string;
}

/** Reads an `operation` event, or null for a body that is not one. */
export function readOperationEvent(data: unknown): OperationEvent | null {
  if (typeof data !== "object" || data === null) return null;
  const body = data as Record<string, unknown>;
  const title = asCoded(body["title"]);
  if (typeof body["id"] !== "string" || title === null || (body["phase"] !== "start" && body["phase"] !== "end")) return null;
  return {
    id: body["id"],
    phase: body["phase"],
    title,
    target: text(body["target"]),
    domain: text(body["domain"]),
    actor: text(body["actor"]),
    at: typeof body["at"] === "string" ? body["at"] : "",
    ...(typeof body["result"] === "string" ? { result: body["result"] } : {}),
  };
}

/** How a server answered the central's question of what it is running. */
export type ServerAnswer = "ok" | "stale" | "unsupported" | "unreachable" | "forbidden" | "error";

/** One server of `GET /api/fleet/jobs/active`: how it answered and what it is running. */
export interface FleetRow {
  /** The server's name on the central. */
  name: string;
  /** This central's own row: its tasks are the console's own server, which has no node name. */
  local: boolean;
  answer: ServerAnswer;
  /** The Noust it runs, when the central knows: what "it will count its tasks when it is updated" names. */
  version: string | null;
  jobs: Job[];
  operations: Operation[];
}

const ANSWERS: ReadonlySet<string> = new Set(["ok", "stale", "unsupported", "unreachable", "forbidden", "error"]);

/**
 * The rows of the fleet view, read field by field: a row is an open object in the contract (each server's
 * own API decides what it holds), so a row that is not what a server sends is skipped, not trusted.
 */
export function readFleetRows(items: readonly Record<string, unknown>[], nodes: readonly { name: string; version?: string | null }[] = []): FleetRow[] {
  return items.flatMap((row): FleetRow[] => {
    const name = text(row["node"]);
    if (name === null) return [];
    const status = text(row["status"]) ?? "ok";
    return [
      {
        name,
        local: row["local"] === true,
        answer: ANSWERS.has(status) ? (status as ServerAnswer) : "error",
        version: nodes.find((node) => node.name === name)?.version ?? null,
        jobs: Array.isArray(row["jobs"]) ? (row["jobs"] as Job[]) : [],
        operations: Array.isArray(row["operations"]) ? (row["operations"] as Operation[]) : [],
      },
    ];
  });
}

// ---------------------------------------------------------------------------------------
// What the panel and the history draw

/** What a task is, said in the order the panel draws it: whose it is, then what runs, then what is done. */
export interface Sections {
  mineRunning: Task[];
  mineQueued: Task[];
  /** What runs or waits on the server that the person did not start. */
  others: Task[];
  /** What ended lately, newest first. */
  done: Task[];
}

/** How far back "ended lately" reaches, and how many of them the panel shows. */
export const RECENT_WINDOW_MS = 24 * 60 * 60 * 1000;
export const RECENT_LIMIT = 5;

function endedAt(task: Task): number {
  return task.completedAt ?? task.touched;
}

/** Oldest start first, so the one that has run longest is on top. */
function byStart(a: Task, b: Task): number {
  return (a.startedAt ?? a.createdAt ?? a.touched) - (b.startedAt ?? b.createdAt ?? b.touched);
}

/**
 * The sections of one server's panel. Yours first (running, then waiting), then what the server does that is
 * not yours, then what ended lately with its result.
 *
 * @param tasks The tasks of one server.
 * @param mine Whether a task is the person's.
 * @param now The present, on the browser's clock.
 */
export function sectionsOf(tasks: readonly Task[], mine: (task: Task) => boolean, now: number): Sections {
  const active = tasks.filter(isActive);
  const done = tasks
    .filter((task) => isFinished(task) && now - endedAt(task) <= RECENT_WINDOW_MS)
    .sort((a, b) => endedAt(b) - endedAt(a))
    .slice(0, RECENT_LIMIT);
  return {
    mineRunning: active.filter((task) => mine(task) && task.state === "running").sort(byStart),
    mineQueued: active.filter((task) => mine(task) && task.state === "queued").sort(byStart),
    others: active.filter((task) => !mine(task)).sort(byStart),
    done,
  };
}

/** The tasks that are running now, which the top bar counts. */
export function runningCount(tasks: readonly Task[]): number {
  return tasks.filter((task) => task.state === "running").length;
}

export function queuedCount(tasks: readonly Task[]): number {
  return tasks.filter((task) => task.state === "queued").length;
}

// ---------------------------------------------------------------------------------------
// Whose it is, and what its page is

/** Who the console is signed in as, in the labels a task's actor can carry. */
export interface Me {
  /** An account's name: how a job it started is labelled, directly or through a central. */
  username: string | null;
  /** The first characters of this browser's session, for a session of the master token (no account). */
  session: string | null;
}

/** The central's label for an operator acting through it: `fleet-fra-1 on behalf of marta`. */
function personBehind(actor: string): string | null {
  const [, ...rest] = actor.split(" on behalf of ");
  const person = rest.join(" on behalf of ").trim();
  return rest.length > 0 && person !== "" ? person : null;
}

/** Whether a task is the signed-in person's: the one they asked for, from any browser, through a central or not. */
export function isMine(task: Pick<Task, "actor">, me: Me): boolean {
  const actor = task.actor;
  if (actor === null) return false;
  if (me.username !== null) return actor === me.username || personBehind(actor) === me.username;
  return me.session !== null && actor === me.session;
}

/** A place a task can be seen: a path of the console, and the node it is on when it is not this server. */
export interface Place {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string>;
}

/** The task's own page: the one Open leads to for a job, and the page of what an operation was about otherwise. */
export function placeOf(task: Pick<Task, "kind" | "id" | "server" | "domain" | "target" | "code">): Place {
  const search = task.server === null ? {} : { search: { node: task.server } };
  if (task.kind === "job") return { to: "/activity/tasks/$id", params: { id: task.id }, ...search };
  if (task.domain !== null) return { to: "/apps/$domain", params: { domain: task.domain }, ...search };
  if (task.code?.startsWith("operations.service_") === true && task.target !== null) {
    return { to: "/server/services/$name", params: { name: task.target }, ...search };
  }
  if (task.code === "operations.cron_run") return { to: "/cron", ...search };
  if (task.code === "operations.backup_verify") return { to: "/backups", ...search };
  return { to: "/activity/tasks", ...search };
}

/** The same place as an address, for a comparison with where the person is. */
export function pathOf(place: Place): string {
  return Object.entries(place.params ?? {}).reduce((path, [name, value]) => path.replace(`$${name}`, encodeURIComponent(value)), place.to);
}

/**
 * Whether the page on screen is the task's: its own page, or the page of the application a job acts on, where the
 * screens that start one follow it today. A task that ends while the person is on one of these says nothing.
 */
export function isOnItsPage(task: Pick<Task, "kind" | "id" | "server" | "domain" | "target" | "code">, pathname: string): boolean {
  const own = pathOf(placeOf(task));
  if (pathname === own || pathname.startsWith(`${own}/`)) return true;
  if (task.domain === null) return false;
  const app = `/apps/${encodeURIComponent(task.domain)}`;
  return pathname === app || pathname.startsWith(`${app}/`);
}
