/**
 * The fleet at once, as the central's one aggregator answers it (`/api/fleet/*`): every
 * server's outcome, whether or not it answered, and the rows each one gave, each labelled with
 * its server and its page there. Nothing here asks a node itself: the central does, in
 * parallel, with a deadline, and serves the last good answer (with its age) of a server that
 * stopped answering. The console only reads what it says and draws it; a node that is down
 * is a row of the answer, never an error of the page.
 *
 * Rows are open objects in the contract (each server's own API decides their fields), so they
 * are read field by field here, once, and every page uses these readers.
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";
import type { Status } from "../../components/ui/StatusPill";
import type { PlainKey } from "../../i18n";

export type FleetView = ResponseOf<"/api/fleet/summary", "get">;
export type NodeOutcome = FleetView["nodes"][number];
export type FleetRow = FleetView["items"][number];
export type FleetResource = "summary" | "servers" | "apps" | "certificates" | "backups" | "updates" | "activity";

export type FleetActions = ResponseOf<"/api/fleet/actions", "get">;
export type FleetActionSpec = FleetActions["actions"][number];
export type FleetActionBody = BodyOf<"/api/fleet/actions", "post">;
export type FleetActionAnswer = ResponseOf<"/api/fleet/actions", "post">;
export type FleetPlan = FleetActionAnswer["plan"];
export type FleetNodeState = FleetPlan["nodes"][number];
export type FleetJob = ResponseOf<"/api/fleet/jobs/{job_id}", "get">;
export type FleetJobList = ResponseOf<"/api/fleet/jobs", "get">;

// ---------------------------------------------------------------------------------------
// Queries

export const fleetKeys = {
  all: ["fleet"] as const,
  view: (resource: FleetResource) => ["fleet", "view", resource] as const,
  actions: ["fleet", "actions"] as const,
  jobs: ["fleet", "jobs"] as const,
  job: (id: string) => ["fleet", "jobs", id] as const,
};

/** How often each view is read again while it is on screen; the central caches about as long. */
export const VIEW_POLL_MS: Readonly<Record<FleetResource, number>> = {
  summary: 15_000,
  servers: 15_000,
  apps: 15_000,
  certificates: 60_000,
  backups: 60_000,
  updates: 60_000,
  activity: 30_000,
};

const VIEW_PATHS = {
  summary: "/api/fleet/summary",
  servers: "/api/fleet/servers",
  apps: "/api/fleet/apps",
  certificates: "/api/fleet/certificates",
  backups: "/api/fleet/backups",
  updates: "/api/fleet/updates",
  activity: "/api/fleet/activity",
} as const;

/**
 * One fleet view. A refresh that fails keeps the last answer on screen (with the error above
 * it), and a view never retries on its own: a central that could not put an answer together
 * says why at once.
 */
export const fleetViewQuery = (resource: FleetResource) =>
  queryOptions({
    queryKey: fleetKeys.view(resource),
    queryFn: ({ signal }) => request("get", VIEW_PATHS[resource], { signal }),
    refetchInterval: VIEW_POLL_MS[resource],
    placeholderData: keepPreviousData,
    retry: false,
  });

/** Asks every server again, whatever the central's cache holds: the operator's "Refresh". */
export function refreshFleetView(resource: FleetResource): Promise<FleetView> {
  return request("get", VIEW_PATHS[resource], { query: { refresh: true } });
}

/** The bulk actions the central offers, with their defaults. */
export const fleetActionsQuery = () =>
  queryOptions({
    queryKey: fleetKeys.actions,
    queryFn: ({ signal }) => request("get", "/api/fleet/actions", { signal }),
    staleTime: Number.POSITIVE_INFINITY,
  });

/** The fleet's own jobs, newest first. */
export const fleetJobsQuery = (limit = 10) =>
  queryOptions({
    queryKey: [...fleetKeys.jobs, limit] as const,
    queryFn: ({ signal }) => request("get", "/api/fleet/jobs", { query: { limit }, signal }),
    refetchInterval: 15_000,
  });

/** How often a running job is read again. */
export const JOB_POLL_MS = 2_000;

/** One fleet job with every server's state, read again every two seconds while it runs. */
export const fleetJobQuery = (id: string) =>
  queryOptions({
    queryKey: fleetKeys.job(id),
    queryFn: ({ signal }) => request("get", "/api/fleet/jobs/{job_id}", { params: { job_id: id }, signal }),
    refetchInterval: (query) => (query.state.data === undefined || isJobRunning(query.state.data) ? JOB_POLL_MS : false),
  });

/** Plans a bulk action (`plan: true`) or starts it as a job. Starting it may ask for sudo mode. */
export function runFleetAction(body: FleetActionBody): Promise<FleetActionAnswer> {
  return request("post", "/api/fleet/actions", { body });
}

/** Plans (or starts) a retry of a job's servers that did not get done. */
export function retryFleetJob(id: string, plan: boolean): Promise<FleetActionAnswer> {
  return request("post", "/api/fleet/jobs/{job_id}/retry", { params: { job_id: id }, query: { plan } });
}

/** Replaces a server's labels. They are this central's own grouping; the server never sees them. */
export function saveLabels(node: string, labels: Record<string, string>): Promise<ResponseOf<"/api/fleet/servers/{node}/labels", "put">> {
  return request("put", "/api/fleet/servers/{node}/labels", { params: { node }, body: { labels } });
}

// ---------------------------------------------------------------------------------------
// Reading an answer

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

export { text as textOf, number as numberOf, object as objectOf };

/** Where a row came from: the server's name, whether it is this central, and its page. */
export interface RowOrigin {
  node: string;
  local: boolean;
  /** The row's page on its server, in this console's address-bar form (`/n/web-2/apps/x`). */
  href: string;
}

export function originOf(row: FleetRow): RowOrigin {
  return {
    node: text(row["node"]) ?? "",
    local: row["local"] === true,
    href: text(row["href"]) ?? "/",
  };
}

/** A row's own field, as text. */
export function field(row: FleetRow, key: string): string | null {
  return text(row[key]);
}

/**
 * How a server answered a view, as the console shows it: its state in the console's
 * vocabulary (colour, shape and word) and whether its rows are missing, old or complete.
 */
export type OutcomeKind = "ok" | "stale" | "unreachable" | "unsupported" | "forbidden" | "error";

export function outcomeKind(outcome: NodeOutcome): OutcomeKind {
  const status = outcome.status;
  return status === "ok" || status === "stale" || status === "unreachable" || status === "unsupported" || status === "forbidden"
    ? status
    : "error";
}

export const OUTCOME_VIEW: Readonly<Record<OutcomeKind, { state: Status; label: PlainKey }>> = {
  ok: { state: "running", label: "fleet.outcome.ok" },
  stale: { state: "warning", label: "fleet.outcome.stale" },
  unreachable: { state: "failed", label: "fleet.outcome.unreachable" },
  // An older Noust is not a fault: grey, and the question mark of what it cannot say.
  unsupported: { state: "unknown", label: "fleet.outcome.unsupported" },
  forbidden: { state: "warning", label: "fleet.outcome.forbidden" },
  error: { state: "failed", label: "fleet.outcome.error" },
};

/** The servers whose answer is not fresh and complete, in the central's order. */
export function troubled(view: FleetView | undefined): NodeOutcome[] {
  return (view?.nodes ?? []).filter((outcome) => outcome.status !== "ok");
}

/** The outcome of one server in a view. */
export function outcomeOf(view: FleetView | undefined, node: string): NodeOutcome | undefined {
  return view?.nodes.find((outcome) => outcome.name === node);
}

/** The server names of a view, this central first, for a server filter. */
export function serverNames(view: FleetView | undefined): string[] {
  const nodes = view?.nodes ?? [];
  return [...nodes.filter((outcome) => outcome.local), ...nodes.filter((outcome) => !outcome.local)].map((outcome) => outcome.name);
}

// ---------------------------------------------------------------------------------------
// The per-server rows (summary, servers, updates)

export interface AccessCeiling {
  level: "read" | "deploy" | "admin";
  hostAccess: boolean;
}

/** The most a server lets this central do there, as it last published it; null when unknown (a 3.0 node, this central). */
export function accessOf(row: FleetRow): AccessCeiling | null {
  const access = object(row["access"]);
  const level = access?.["level"];
  if (level !== "read" && level !== "deploy" && level !== "admin") return null;
  return { level, hostAccess: access?.["host_access"] === true };
}

/** A server's labels (`env=prod`), sorted by key. */
export function labelsOf(row: FleetRow): [string, string][] {
  const labels = object(row["labels"]) ?? {};
  return Object.entries(labels)
    .filter((entry): entry is [string, string] => typeof entry[1] === "string")
    .sort(([a], [b]) => a.localeCompare(b));
}

export interface Counts {
  running: number;
  failed: number;
  stopped: number;
  static: number;
}

/** A block of counters (`apps`, `units`), or null when the server did not give it. */
export function countsOf(row: FleetRow, key: "apps" | "units"): Counts | null {
  const block = object(row[key]);
  if (block === null) return null;
  const read = (name: string): number => number(block[name]) ?? 0;
  return { running: read("running"), failed: read("failed"), stopped: read("stopped"), static: read("static") };
}

export type NoustUpdateState = "up_to_date" | "update_available" | "on_the_way" | "unknown";

export interface NoustVersion {
  current: string | null;
  latest: string | null;
  state: NoustUpdateState;
  command: string | null;
  method: string | null;
}

/** What a server says about its own Noust: installed, available, and how it updates. */
export function noustOf(row: FleetRow): NoustVersion {
  const noust = object(row["noust"]) ?? {};
  const state = noust["update_state"];
  return {
    current: text(noust["current_version"]) ?? text(row["version"]),
    latest: text(noust["latest_version"]) ?? text(noust["published_version"]),
    state: state === "up_to_date" || state === "update_available" || state === "on_the_way" ? state : "unknown",
    command: text(noust["update_command"]),
    method: text(noust["method"]),
  };
}

export interface OsUpdates {
  pending: number | null;
  security: number | null;
  reboot: boolean | null;
  os: string | null;
}

/** A server's operating system updates, from its own server summary; null when it gave none. */
export function osUpdatesOf(row: FleetRow): OsUpdates | null {
  const os = object(row["os"]);
  if (os === null) return null;
  const updates = object(os["updates"]);
  const reboot = object(os["reboot"]);
  const system = object(os["os"]);
  return {
    pending: number(updates?.["pending"]) ?? number(updates?.["total"]) ?? number(updates?.["count"]),
    security: number(updates?.["security"]),
    reboot: typeof reboot?.["required"] === "boolean" ? reboot["required"] : typeof os["reboot"] === "boolean" ? os["reboot"] : null,
    os: text(system?.["name"]) ?? text(system?.["pretty_name"]),
  };
}

/** A server's own "needs attention" list, from its Overview; null for a server too old to give one. */
export function attentionOf(row: FleetRow): { items: AttentionEntry[]; total: number } | null {
  const overview = object(row["overview"]);
  if (overview === null) return null;
  const list = Array.isArray(overview["attention"]) ? overview["attention"] : [];
  const items = list.flatMap((raw): AttentionEntry[] => {
    const entry = object(raw);
    if (entry === null) return [];
    const reasons = Array.isArray(entry["reasons"]) ? entry["reasons"].map(object).filter((reason) => reason !== null) : [];
    return [
      {
        id: text(entry["id"]) ?? "",
        title: text(entry["title"]) ?? "",
        severity: entry["severity"] === "fail" ? "fail" : "warn",
        subject: object(entry["subject"]) ?? {},
        reasons,
      },
    ];
  });
  return { items, total: number(overview["attention_total"]) ?? items.length };
}

export interface AttentionEntry {
  id: string;
  title: string;
  severity: "fail" | "warn";
  subject: Record<string, unknown>;
  reasons: Record<string, unknown>[];
}

// ---------------------------------------------------------------------------------------
// Jobs

/** A fleet job still going: some server is queued or running. */
export function isJobRunning(job: Pick<FleetJob, "status">): boolean {
  return job.status === "running" || job.status === "queued";
}

export type NodeJobState = "queued" | "running" | "succeeded" | "failed" | "skipped" | "unreachable" | "refused" | "interrupted" | "cancelled";

export function nodeJobState(state: string): NodeJobState {
  switch (state) {
    case "queued":
    case "running":
    case "succeeded":
    case "failed":
    case "skipped":
    case "unreachable":
    case "refused":
    case "interrupted":
    case "cancelled":
      return state;
    default:
      return "failed";
  }
}

/** Each server's state in a job, told with the console's states: colour, shape and word. */
export const NODE_JOB_VIEW: Readonly<Record<NodeJobState, { state: Status; label: PlainKey }>> = {
  queued: { state: "queued", label: "fleet.jobs.state.queued" },
  running: { state: "deploying", label: "fleet.jobs.state.running" },
  succeeded: { state: "running", label: "fleet.jobs.state.succeeded" },
  failed: { state: "failed", label: "fleet.jobs.state.failed" },
  skipped: { state: "stopped", label: "fleet.jobs.state.skipped" },
  unreachable: { state: "failed", label: "fleet.jobs.state.unreachable" },
  refused: { state: "failed", label: "fleet.jobs.state.refused" },
  interrupted: { state: "warning", label: "fleet.jobs.state.interrupted" },
  cancelled: { state: "stopped", label: "fleet.jobs.state.cancelled" },
};

/** The whole job's state. */
export const JOB_STATUS_VIEW: Readonly<Record<string, { state: Status; label: PlainKey }>> = {
  queued: { state: "queued", label: "fleet.jobs.status.queued" },
  running: { state: "deploying", label: "fleet.jobs.status.running" },
  succeeded: { state: "running", label: "fleet.jobs.status.succeeded" },
  failed: { state: "failed", label: "fleet.jobs.status.failed" },
  aborted: { state: "failed", label: "fleet.jobs.status.aborted" },
  interrupted: { state: "warning", label: "fleet.jobs.status.interrupted" },
};

export function jobStatusView(status: string): { state: Status; label: PlainKey } {
  return JOB_STATUS_VIEW[status] ?? { state: "unknown", label: "fleet.jobs.status.unknown" };
}

/** Why a server was skipped, in words; a reason this console does not know yet, verbatim. */
export const SKIP_REASONS: Readonly<Record<string, PlainKey>> = {
  policy: "fleet.jobs.reason.policy",
  unsupported: "fleet.jobs.reason.unsupported",
  not_needed: "fleet.jobs.reason.notNeeded",
  aborted: "fleet.jobs.reason.aborted",
  busy: "fleet.jobs.reason.busy",
  elevation_expired: "fleet.jobs.reason.elevationExpired",
  unreachable: "fleet.jobs.reason.unreachable",
  error: "fleet.jobs.reason.error",
};

/** A job's server states that a retry would run again. */
export function retryable(job: Pick<FleetJob, "nodes" | "status">): number {
  if (isJobRunning(job)) return 0;
  return (job.nodes ?? []).filter((node) => {
    const state = nodeJobState(node.state);
    if (state === "failed" || state === "unreachable" || state === "refused" || state === "interrupted") return true;
    return state === "skipped" && (node.reason === "aborted" || node.reason === "busy" || node.reason === "elevation_expired");
  }).length;
}

/** The error a server's step failed with: the central's sentence and hint, and the node's words. */
export function nodeErrorOf(node: FleetNodeState): { message: string | null; hint: string | null; code: string | null } {
  const error = node.error ?? {};
  return { message: text(error["message"]) ?? text(error["detail"]), hint: text(error["hint"]), code: text(error["code"]) ?? text(error["error"]) };
}

// ---------------------------------------------------------------------------------------
// The actions

/** The bulk actions this console knows how to describe, in the order it offers them. */
export const ACTION_ORDER = ["certs_renew", "backups_run", "backups_verify", "apps_update", "apps_restart", "noust_update", "os_updates"] as const;
export type KnownAction = (typeof ACTION_ORDER)[number];

export function isKnownAction(name: string): name is KnownAction {
  return (ACTION_ORDER as readonly string[]).includes(name);
}

/** Each action's name and what it does, in the operator's words. */
export const ACTION_WORDS: Readonly<Record<KnownAction, { label: PlainKey; description: PlainKey }>> = {
  certs_renew: { label: "fleet.actions.certs_renew.label", description: "fleet.actions.certs_renew.description" },
  backups_run: { label: "fleet.actions.backups_run.label", description: "fleet.actions.backups_run.description" },
  backups_verify: { label: "fleet.actions.backups_verify.label", description: "fleet.actions.backups_verify.description" },
  apps_update: { label: "fleet.actions.apps_update.label", description: "fleet.actions.apps_update.description" },
  apps_restart: { label: "fleet.actions.apps_restart.label", description: "fleet.actions.apps_restart.description" },
  noust_update: { label: "fleet.actions.noust_update.label", description: "fleet.actions.noust_update.description" },
  os_updates: { label: "fleet.actions.os_updates.label", description: "fleet.actions.os_updates.description" },
};

/** A label selector as typed: `env=prod, team=web`. Null when a pair is not `key=value`. */
export function parseLabelSelector(value: string): Record<string, string> | null {
  const out: Record<string, string> = {};
  for (const part of value.split(/[,\s]+/)) {
    if (part === "") continue;
    const at = part.indexOf("=");
    if (at <= 0 || at === part.length - 1) return null;
    out[part.slice(0, at)] = part.slice(at + 1);
  }
  return out;
}

/** The labels as they are typed back: `env=prod, team=web`. */
export function formatLabels(labels: Readonly<Record<string, string>> | readonly (readonly [string, string])[]): string {
  const entries = Array.isArray(labels) ? labels : Object.entries(labels as Record<string, string>);
  return entries.map(([key, value]) => `${key}=${value}`).join(", ");
}

/** A label key or value as the central keeps it: letters, digits, dots, dashes and underscores. */
export const LABEL_PART = /^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$/;

/** Whether a server carries every label of a selector. */
export function matchesLabels(labels: readonly (readonly [string, string])[], selector: Readonly<Record<string, string>>): boolean {
  const own = new Map(labels);
  return Object.entries(selector).every(([key, value]) => own.get(key) === value);
}

/**
 * The batch a server runs in, counted from 1, and whether it is the canary. The engine numbers
 * batches from 0, and batch 0 is the canary's only when there is one.
 */
export function batchOf(node: Pick<FleetNodeState, "batch" | "node">, canary: unknown): { canary: boolean; number: number } {
  const hasCanary = typeof canary === "string" && canary !== "";
  if (hasCanary && node.node === canary) return { canary: true, number: 0 };
  return { canary: false, number: hasCanary ? node.batch : node.batch + 1 };
}
