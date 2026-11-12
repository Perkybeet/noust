/**
 * The one server-sent event stream, `/events`.
 *
 * Every tab holds exactly one EventSource (a browser caps connections per origin, and the
 * backend multiplexes every concern onto this stream). Events land in two places: the
 * TanStack Query cache, so any page showing the data updates without polling, and the
 * subscribers of `useServerEvent`, for pages that react to a moment (a deploy finishing)
 * rather than to data.
 *
 * On a central with a node selected, the stream is the node's, relayed by the central
 * (`/api/nodes/{node}/events`), and what it says is written into that node's cache entries.
 *
 * The browser's own EventSource retry runs at a fixed interval forever; this closes the
 * source on error and reconnects on an exponential backoff instead, and checks whether the
 * session is still alive, because an EventSource cannot see the 401 that ended it.
 */

import { useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useSyncExternalStore } from "react";

import { expireSession } from "../api/client";
import { activeNode, nodeEventsPath, runOnNode } from "../api/nodeScope";
import { appKeys } from "../api/queries/apps";
import type { App } from "../api/queries/apps";
import { sessionQuery } from "../api/queries/auth";
import { certKeys } from "../api/queries/certs";
import { databaseKeys } from "../api/queries/databases";
import { deploymentKeys } from "../api/queries/deployments";
import { firstDeployKeys } from "../api/queries/firstDeploys";
import { jobKeys } from "../api/queries/jobs";
import type { Job } from "../api/queries/jobs";
import { metricKeys } from "../api/queries/metrics";
import type { MetricsSnapshot } from "../api/queries/metrics";
import { attentionKeys } from "../api/queries/attention";
import { overviewKeys } from "../api/queries/overview";
import { queueKeys } from "../api/queries/queue";
import { serviceKeys } from "../api/queries/services";
import { siteKeys } from "../api/queries/sites";
import { systemKeys } from "../api/queries/system";
import type { Machine } from "../api/queries/system";
import { getLocale } from "../app/locale";
import { toast } from "../components/ui/toast";
import { hasNamespace, loadNamespaces } from "../i18n/catalogs";
import { leadingSentence } from "../lib/serverSentence";
import type { CodedText } from "../lib/serverSentence";
import { takeJobEndHandled } from "./jobEnds";
import { useNode } from "../nodes/useNode";
import { reconnectDelay } from "./backoff";
import { recordNotice } from "./notices";

/** A state change of one application. Carries its domain and the fields that changed. */
export type AppEvent = { domain: string } & Partial<Exclude<App, undefined>>;

/** A job snapshot, the same shape GET /api/jobs/{id} answers. */
export type JobEvent = Job;

/** Something the operator should hear about, in the system's words. */
export interface NoticeEvent {
  text: string;
  /** The backend's four-word vocabulary: busy, active, failed, idle. */
  state: "busy" | "active" | "failed" | "idle";
  /** `text` as a sentence with a code, when it is one of Noust's own (a fleet job's title): said in the console's language. */
  coded?: CodedText | null;
}

/** The pre-v2 transition event: an id (a job id or a domain) moved to a state. */
export interface StateEvent {
  id: string;
  state: NoticeEvent["state"];
}

export interface ServerEventMap {
  machine: Machine;
  metrics: MetricsSnapshot;
  app: AppEvent;
  job: JobEvent;
  /** A long operation that answers on its request began or ended (`phase`): read by the task centre, which keeps no cache entry for it. */
  operation: Record<string, unknown>;
  notice: NoticeEvent;
  state: StateEvent;
}

export type ServerEventName = keyof ServerEventMap;

export const SERVER_EVENTS: readonly ServerEventName[] = ["machine", "metrics", "app", "job", "operation", "notice", "state"];

export type StreamStatus = "connecting" | "live" | "reconnecting";

/** The part of EventSource the stream uses, so tests can drive it. */
export interface EventSourceLike {
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
  close(): void;
}

export interface EventStreamOptions {
  url?: string;
  connect?: (url: string) => EventSourceLike;
  onEvent: (name: ServerEventName, data: unknown) => void;
  onStatus?: (status: StreamStatus) => void;
  /** Called after every dropped connection with the count of consecutive failures. */
  onDrop?: (failures: number) => void;
}

/** One EventSource with exponential reconnection. Framework-free; the hook below drives it. */
export class EventStream {
  private readonly url: string;
  private readonly connect: (url: string) => EventSourceLike;
  private readonly options: EventStreamOptions;
  private source: EventSourceLike | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private failures = 0;
  private running = false;

  constructor(options: EventStreamOptions) {
    this.options = options;
    this.url = options.url ?? "/events";
    this.connect = options.connect ?? ((url) => new EventSource(url, { withCredentials: true }));
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.failures = 0;
    this.open();
  }

  stop(): void {
    this.running = false;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.source?.close();
    this.source = null;
  }

  private open(): void {
    this.timer = null;
    this.options.onStatus?.(this.failures === 0 ? "connecting" : "reconnecting");
    const source = this.connect(this.url);
    this.source = source;

    source.onopen = () => {
      this.failures = 0;
      this.options.onStatus?.("live");
    };
    source.onerror = () => {
      source.close();
      if (!this.running || this.source !== source) return;
      this.source = null;
      const delay = reconnectDelay(this.failures);
      this.failures += 1;
      this.options.onStatus?.("reconnecting");
      this.options.onDrop?.(this.failures);
      this.timer = setTimeout(() => {
        this.open();
      }, delay);
    };
    for (const name of SERVER_EVENTS) {
      source.addEventListener(name, (event) => {
        let data: unknown;
        try {
          data = JSON.parse(event.data);
        } catch {
          // A frame that is not JSON is a backend bug; drop it loudly, keep the stream.
          console.error(`Ignored a malformed "${name}" event from /events:`, event.data);
          return;
        }
        this.options.onEvent(name, data);
      });
    }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const TERMINAL_JOB_STATES = new Set(["completed", "failed", "cancelled"]);

/**
 * Whether a job's place in the queue changed while its status did not: it moved up the line,
 * what it waits for changed, or it was asked to stop and still runs. The `job` event carries
 * all three; a progress tick changes none of them. (Whether it can still be cancelled is not
 * in the event: the queue's own read says, and is asked again while anything is in it.)
 */
function queueMoved(previous: Job, next: Job): boolean {
  return (
    previous.queue_position !== next.queue_position ||
    previous.waiting_for !== next.waiting_for ||
    previous.cancel_requested !== next.cancel_requested
  );
}

/**
 * The machine-wide lists a job can add to or take from, and the command palette indexes: the
 * prefixes of every read of them, so each page's own filtered read goes stale with them.
 */
const RESOURCE_LIST_KEYS = [databaseKeys.lists, siteKeys.all, certKeys.all, serviceKeys.all] as const;

/** The longest a failure's title is: a toast is a notice, not the page. */
const FAILURE_TITLE_MAX = 120;

/**
 * A failure's words as a toast shows them. A failed deploy's notice carries what the tool printed, whole (an
 * npm error is a screenful), and a toast that held all of it covered the page. The first line is the title and,
 * when there is more, all of it is beside it, verbatim, in a box that scrolls: the toast stays the size of a
 * notice, it is closed with its own button, and nothing is lost.
 */
export function failureWords(text: string): { title: string; detail?: string } {
  const whole = text.trim();
  const first = (whole.split("\n")[0] ?? "").trim();
  const title = first.length > FAILURE_TITLE_MAX ? `${first.slice(0, FAILURE_TITLE_MAX - 1).trimEnd()}…` : first;
  return title === whole ? { title } : { title, detail: whole };
}

/**
 * Writes one server event into the query cache. Pure over the client passed in, so every
 * cache effect of the stream is tested without a stream.
 */
export function applyServerEvent(queryClient: QueryClient, name: ServerEventName, data: unknown): void {
  if (!isRecord(data)) return;
  switch (name) {
    case "machine":
      queryClient.setQueryData<Machine>(systemKeys.machine, data as unknown as Machine);
      return;
    case "metrics":
      queryClient.setQueryData<MetricsSnapshot>(metricKeys.latest, data as MetricsSnapshot);
      return;
    case "app": {
      const { domain, ...changes } = data;
      if (typeof domain !== "string") return;
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      // An application that changed state may have a deployment it did not have: its history is read again.
      void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
      // What needs the operator is read from the one list the server joins (the inbox and the bar's counters): an application that changed may have started or stopped needing it.
      void queryClient.invalidateQueries({ queryKey: overviewKeys.all });
      void queryClient.invalidateQueries({ queryKey: attentionKeys.all });
      queryClient.setQueryData(appKeys.detail(domain), (current: unknown) =>
        isRecord(current) ? { ...current, ...changes } : current,
      );
      return;
    }
    case "job": {
      const job = data as unknown as Job;
      if (typeof job.id !== "string") return;
      const previous = queryClient.getQueryData<Job>(jobKeys.detail(job.id));
      queryClient.setQueryData(jobKeys.detail(job.id), job);
      // A job that waits moves up the line, or is asked to stop, without changing status: the
      // queue and the jobs in hand say where it stands, so they are read again for that.
      if (previous?.status === job.status && queueMoved(previous, job)) {
        void queryClient.invalidateQueries({ queryKey: queueKeys.all });
        void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      }
      // The deployment a job records is written a moment after the job starts, by the deployer, and no event says
      // so: a deploy a push started would otherwise be missing from the application's history until the page was
      // read again. Each step the job moves to (a few in a deploy, never a log line) reads the history once more.
      if (typeof job.metadata?.["domain"] === "string" && previous?.current_step !== job.current_step) {
        void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
      }
      // Progress and log lines arrive as job events too; only a change of status changes a
      // list, so only that refetches one.
      if (previous?.status === job.status) return;
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
      void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
      void queryClient.invalidateQueries({ queryKey: overviewKeys.all });
      void queryClient.invalidateQueries({ queryKey: attentionKeys.all });
      // A first deployment that failed leaves no application: the page of its domain finds the failure here.
      void queryClient.invalidateQueries({ queryKey: firstDeployKeys.all });
      const domain = job.metadata?.["domain"];
      if (typeof domain === "string" && TERMINAL_JOB_STATES.has(job.status)) {
        void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain) });
      }
      // What a job leaves behind (a database, a certificate, a site, a service) is what the
      // command palette searches: marked stale, it is read again the next time it is opened.
      // Not refetched now: a page that is open keeps refreshing the way it always did, and the
      // palette, closed, reads nothing.
      if (TERMINAL_JOB_STATES.has(job.status)) {
        for (const queryKey of RESOURCE_LIST_KEYS) void queryClient.invalidateQueries({ queryKey, refetchType: "none" });
      }
      return;
    }
    case "operation":
      // The task centre's store is what holds it (features/tasks/feeds.ts): nothing in the cache is about one.
      return;
    case "notice": {
      const sent = typeof data["text"] === "string" ? data["text"] : null;
      if (sent === null) return;
      // The end of a job of the person's is said by the task centre, with a way to see it, or not at all on its page: said
      // once, in one channel (docs/DESIGN.md, 6.12). Its mark is taken now, in the frame order the server sent.
      const handled = takeJobEndHandled(typeof data["state"] === "string" ? data["state"] : "");
      // A sentence of Noust's own, in this console's language; what a tool said is shown as it came, so a failure
      // whose first line is one keeps everything after it.
      const coded = isRecord(data["coded"]) ? (data["coded"] as unknown as CodedText) : null;
      const state = data["state"];
      const node = activeNode();
      const say = (): void => {
        const text = leadingSentence(getLocale(), coded, sent);
        // The toast is gone in seconds; the inbox keeps the last thirty (NV-17).
        recordNotice(text, state === "failed" || state === "active" || state === "busy" ? state : "idle", node);
        if (handled) return;
        if (state === "failed") {
          const { title, detail } = failureWords(text);
          if (detail === undefined) toast.error(title);
          else toast.error(title, { detail });
        }
        // design-exception: toast-success a notice the server sends is, by definition, something that is not on this screen
        else if (state === "active") toast.success(text);
        else toast.info(text);
      };
      // The texts of the server's sentences are fetched the first time one is asked for, and a toast is said once: it waits
      // for them, or it would be said in English in a Spanish console the first time. When they do not come, the English
      // the server sent is what is said.
      const locale = getLocale();
      if (coded !== null && !(hasNamespace("en", "serverText") && hasNamespace(locale, "serverText"))) {
        void Promise.all([loadNamespaces("en", ["serverText"]), loadNamespaces(locale, ["serverText"])]).then(say, say);
      } else {
        say();
      }
      return;
    }
    case "state": {
      if (typeof data["id"] !== "string") return;
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(data["id"]) });
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
      void queryClient.invalidateQueries({ queryKey: overviewKeys.all });
      void queryClient.invalidateQueries({ queryKey: attentionKeys.all });
      return;
    }
  }
}

// ---------------------------------------------------------------------------------------
// React bindings.

type Handler<N extends ServerEventName> = (data: ServerEventMap[N]) => void;

const subscribers = new Map<ServerEventName, Set<Handler<ServerEventName>>>();
let streamStatus: StreamStatus = "connecting";
const statusListeners = new Set<() => void>();

function setStreamStatus(next: StreamStatus): void {
  if (next === streamStatus) return;
  streamStatus = next;
  for (const listener of statusListeners) listener();
}

function publish(name: ServerEventName, data: unknown): void {
  for (const handler of subscribers.get(name) ?? []) handler(data as ServerEventMap[ServerEventName]);
}

/**
 * Opens the stream for as long as the calling component is mounted. Mount it once, in the
 * authenticated shell: /events needs a session.
 */
export function useServerEvents(connect?: (url: string) => EventSourceLike): void {
  const queryClient = useQueryClient();
  const connectRef = useRef(connect);
  const { node } = useNode();

  useEffect(() => {
    // Whatever happened while the stream was down (a job ending, an app failing) was said on
    // a connection nobody held: once it is back, everything on screen is read again.
    let dropped = false;
    const stream = new EventStream({
      url: nodeEventsPath(node),
      ...(connectRef.current ? { connect: connectRef.current } : {}),
      onEvent: (name, data) => {
        // Into the entries of the server that said it, even mid-switch.
        runOnNode(node, () => {
          applyServerEvent(queryClient, name, data);
        });
        publish(name, data);
      },
      onStatus: (status) => {
        setStreamStatus(status);
        if (status === "reconnecting") dropped = true;
        else if (status === "live" && dropped) {
          dropped = false;
          void queryClient.invalidateQueries();
        }
      },
      onDrop: () => {
        // The stream cannot tell a restarting panel from an expired session. The session
        // endpoint can: it answers 200 either way, saying which.
        queryClient
          .query({ ...sessionQuery(), staleTime: 0 })
          .then((session) => {
            if (!session.authenticated) expireSession();
          })
          // Unreachable server: the strip already says "Reconnecting", and the stream retries.
          .catch(() => undefined);
      },
    });
    stream.start();
    return () => {
      stream.stop();
      setStreamStatus("connecting");
    };
  }, [queryClient, node]);
}

/** Runs `handler` for every event of one name while the component is mounted. */
export function useServerEvent<N extends ServerEventName>(name: N, handler: Handler<N>): void {
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  useEffect(() => {
    const relay: Handler<ServerEventName> = (data) => {
      ref.current(data as ServerEventMap[N]);
    };
    let set = subscribers.get(name);
    if (!set) {
      set = new Set();
      subscribers.set(name, set);
    }
    set.add(relay);
    return () => {
      set.delete(relay);
    };
  }, [name]);
}

/** Whether the live stream is up, for the strip's reconnecting indicator. */
export function useStreamStatus(): StreamStatus {
  return useSyncExternalStore(
    (listener) => {
      statusListeners.add(listener);
      return () => statusListeners.delete(listener);
    },
    () => streamStatus,
  );
}
