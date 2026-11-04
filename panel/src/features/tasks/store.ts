/**
 * The task centre's one store (item 114): every task of every server the console has heard of, outliving
 * every page. The sources write into it (`feeds.ts`: the active jobs, the history, the `job` and `operation`
 * events, the operations that run, and a central's view of its fleet) and the top bar, its panel, the pinned
 * chips, a task's page and the history read it, so no screen follows a job in a state of its own to tell the
 * centre about it.
 *
 * The reducers are pure functions over a {@link TaskState}; the store is the thin cell that holds the current
 * one and tells its readers (`useSyncExternalStore`). A test makes a store of its own with {@link createTaskStore}
 * and never touches the console's.
 *
 * Two writers race for a task, as they do for a job's query entry: an event and a read. A task that has ended is
 * never taken back to running by a read that was answered just before it ended, and a read that is a whole list
 * ("what runs now") is the authority on what is *not* running only for what the console had not heard of since
 * the read began.
 */

import { useSyncExternalStore } from "react";

import type { Job } from "../../api/queries/jobs";
import type { Operation } from "../../api/queries/operations";
import { isActive, isFinished, readFleetRows, taskKey, taskOfJob, taskOfOperation } from "./model";
import type { FleetRow, OperationEvent, ServerAnswer, ServerKey, Task } from "./model";

/** How a server of the fleet answered, and what it is called. */
export interface ServerStatus {
  name: string;
  answer: ServerAnswer;
  version: string | null;
}

export interface TaskState {
  tasks: ReadonlyMap<string, Task>;
  /** What each server of a fleet said last (only a central asks). */
  servers: ReadonlyMap<string, ServerStatus>;
  /** The keys of the tasks the person pinned, oldest first. */
  pinned: readonly string[];
}

export const EMPTY: TaskState = { tasks: new Map(), servers: new Map(), pinned: [] };

/** The most tasks held: what has ended and is oldest goes first, and what runs never does. */
export const KEEP = 200;

function serverId(server: ServerKey): string {
  return server ?? "";
}

function prune(tasks: Map<string, Task>): Map<string, Task> {
  if (tasks.size <= KEEP) return tasks;
  const ended = [...tasks.values()].filter(isFinished).sort((a, b) => a.touched - b.touched);
  for (const task of ended.slice(0, tasks.size - KEEP)) tasks.delete(task.key);
  return tasks;
}

/**
 * Takes a task in. A task that has ended stays ended against a snapshot that says it still runs (the answer of a
 * read that was in flight when the event came), and its place in the queue is the newer word.
 */
function merged(previous: Task | undefined, next: Task): Task {
  if (previous === undefined) return next;
  if (isFinished(previous) && isActive(next)) return previous;
  // An event that does not carry what a read does (a job's `cancellable`) leaves what the read said.
  return { ...next, cancellable: next.cancellable ?? previous.cancellable, createdAt: next.createdAt ?? previous.createdAt };
}

function put(state: TaskState, task: Task): TaskState {
  const tasks = new Map(state.tasks);
  tasks.set(task.key, merged(state.tasks.get(task.key), task));
  return { ...state, tasks: prune(tasks) };
}

/** A job the `job` event reports: the newest word there is. */
export function withJob(state: TaskState, job: Job, server: ServerKey, now: number): TaskState {
  return put(state, taskOfJob(job, server, now));
}

/**
 * A task a read reports. What the console heard of it after the read began (an event) is newer than the answer, so the
 * answer does not take its place.
 */
function putRead(state: TaskState, task: Task, startedAt: number): TaskState {
  const held = state.tasks.get(task.key);
  return held !== undefined && held.touched >= startedAt ? state : put(state, task);
}

/** Jobs from the history: ended ones, and anything that still runs. The list says nothing of what is not in it. */
export function withJobs(state: TaskState, jobs: readonly Job[], server: ServerKey, now: number, startedAt: number): TaskState {
  return jobs.reduce((next, job) => putRead(next, taskOfJob(job, server, now), startedAt), state);
}

/**
 * The jobs a server runs or has waiting, as one list that is the whole of it: they are taken in, and a job of
 * that server that the console held as active and the list does not have, which the console had not heard of
 * since the read began (`startedAt`), ended without its event reaching it and is let go. The history says how.
 */
export function withActiveJobs(state: TaskState, jobs: readonly Job[], server: ServerKey, now: number, startedAt: number): TaskState {
  const present = new Set(jobs.map((job) => taskKey(server, "job", job.id)));
  const tasks = new Map(state.tasks);
  for (const task of state.tasks.values()) {
    if (task.kind === "job" && task.server === server && isActive(task) && !present.has(task.key) && task.touched < startedAt) tasks.delete(task.key);
  }
  const kept: TaskState = { ...state, tasks };
  return jobs.reduce((next, job) => putRead(next, taskOfJob(job, server, now), startedAt), kept);
}

/** The operations that run now, as one list that is the whole of it, on the same terms as {@link withActiveJobs}. */
export function withOperations(state: TaskState, operations: readonly Operation[], server: ServerKey, now: number, startedAt: number): TaskState {
  const present = new Set(operations.map((operation) => taskKey(server, "operation", operation.id)));
  const tasks = new Map(state.tasks);
  for (const task of state.tasks.values()) {
    if (task.kind === "operation" && task.server === server && isActive(task) && !present.has(task.key) && task.touched < startedAt) tasks.delete(task.key);
  }
  const kept: TaskState = { ...state, tasks };
  return operations.reduce((next, operation) => putRead(next, taskOfOperation(operation, server, now), startedAt), kept);
}

/** An `operation` event: it starts and is running, or it ends and keeps its result for the panel's "ended lately". */
export function withOperationEvent(state: TaskState, event: OperationEvent, server: ServerKey, now: number): TaskState {
  const key = taskKey(server, "operation", event.id);
  const known = state.tasks.get(key);
  if (event.phase === "start") {
    if (known !== undefined) return state;
    return put(state, taskOfOperation({ id: event.id, title: event.title, target: event.target, domain: event.domain, actor: event.actor, started_at: event.at }, server, now));
  }
  const base = known ?? taskOfOperation({ id: event.id, title: event.title, target: event.target, domain: event.domain, actor: event.actor, started_at: event.at }, server, now);
  const ended: Task = { ...base, state: event.result === "failed" ? "failed" : "succeeded", completedAt: Date.parse(event.at) || now, touched: now };
  const tasks = new Map(state.tasks);
  tasks.set(key, ended);
  return { ...state, tasks: prune(tasks) };
}

/** What a central says of its fleet: each server's tasks replace what the console held of it, and how it answered is kept. */
export function withFleet(state: TaskState, rows: readonly FleetRow[], now: number, startedAt: number): TaskState {
  const servers = new Map(state.servers);
  let next: TaskState = state;
  for (const row of rows) {
    const server: ServerKey = row.local ? null : row.name;
    servers.set(serverId(server), { name: row.name, answer: row.answer, version: row.version });
    if (row.answer === "ok" || row.answer === "stale") {
      next = withOperations(withActiveJobs(next, row.jobs, server, now, startedAt), row.operations, server, now, startedAt);
    } else {
      // A server that is down, old or refusing says nothing: what the console held of it is not shown as running.
      const tasks = new Map(next.tasks);
      for (const task of next.tasks.values()) if (task.server === server && isActive(task) && task.touched < startedAt) tasks.delete(task.key);
      next = { ...next, tasks };
    }
  }
  return { ...next, servers };
}

/** Reads the fleet view's rows and takes them in. */
export function withFleetItems(
  state: TaskState,
  items: readonly Record<string, unknown>[],
  nodes: readonly { name: string; version?: string | null }[],
  now: number,
  startedAt: number,
): TaskState {
  return withFleet(state, readFleetRows(items, nodes), now, startedAt);
}

export function withPinned(state: TaskState, key: string, pinned: boolean): TaskState {
  const has = state.pinned.includes(key);
  if (pinned === has) return state;
  return { ...state, pinned: pinned ? [...state.pinned, key] : state.pinned.filter((candidate) => candidate !== key) };
}

// ---------------------------------------------------------------------------------------
// The cell

export interface TaskStore {
  get: () => TaskState;
  subscribe: (listener: () => void) => () => void;
  /** Applies a reducer. A reducer that returns the state it was given tells nobody. */
  update: (reducer: (state: TaskState) => TaskState) => void;
  /** Forgets everything. For tests. */
  reset: () => void;
}

export function createTaskStore(initial: TaskState = EMPTY, onChange?: (state: TaskState, previous: TaskState) => void): TaskStore {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    update: (reducer) => {
      const next = reducer(state);
      if (next === state) return;
      const previous = state;
      state = next;
      onChange?.(next, previous);
      for (const listener of [...listeners]) listener();
    },
    reset: () => {
      const previous = state;
      state = initial;
      onChange?.(state, previous);
      for (const listener of [...listeners]) listener();
    },
  };
}

// ---------------------------------------------------------------------------------------
// The pins live as long as the browser does: a reload keeps them.

const PINNED_KEY = "noust.tasks.pinned";

export function readPinned(storage: Pick<Storage, "getItem"> = window.localStorage): string[] {
  try {
    const raw = storage.getItem(PINNED_KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((entry): entry is string => typeof entry === "string") : [];
  } catch {
    // A value somebody else wrote there: no pins is the safe reading.
    return [];
  }
}

function writePinned(pinned: readonly string[]): void {
  try {
    window.localStorage.setItem(PINNED_KEY, JSON.stringify(pinned));
  } catch {
    // Storage may be full or refused: the pin lasts until the tab closes, which is what it can do.
  }
}

/** The console's own store. */
export const taskStore: TaskStore = createTaskStore(typeof window === "undefined" ? EMPTY : { ...EMPTY, pinned: readPinned() }, (next, previous) => {
  if (next.pinned !== previous.pinned) writePinned(next.pinned);
});

export function useTaskState(store: TaskStore = taskStore): TaskState {
  return useSyncExternalStore(store.subscribe, store.get);
}

/** Pins a task: its chip follows the person from page to page until they let it go. Nothing pins itself. */
export function pin(key: string, store: TaskStore = taskStore): void {
  store.update((state) => withPinned(state, key, true));
}

export function unpin(key: string, store: TaskStore = taskStore): void {
  store.update((state) => withPinned(state, key, false));
}
