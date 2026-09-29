/**
 * The Activity page's own model: one merged, newest-first timeline of the jobs history and the
 * audit log, and the words each row is shown in.
 *
 * The two sources paginate differently - jobs are a flat "top N" list with no cursor, the
 * audit log is walked backward with a real one (`before`/`next_before`) - so merging them
 * safely for "Load more" takes care; see `mergeActivity` below.
 */

import { deployStatus } from "../../components/page/status";
import type { StatusView } from "../../components/page/status";
import type { AuditEntry } from "../../api/queries/audit";
import type { JobList } from "../../api/queries/jobs";
import type { PlainKey, T } from "../../i18n";
import { parseTimestamp } from "../../lib/format";

export type { AuditEntry } from "../../api/queries/audit";
export type ActivityJob = JobList["jobs"][number];

// ---------------------------------------------------------------------------------------
// Rows: a job and an audit entry are different shapes, merged into one discriminated union
// so the table and the merge logic below can treat them uniformly.

export interface JobRow {
  kind: "job";
  id: string;
  timestamp: string;
  job: ActivityJob;
}

export interface AuditRow {
  kind: "audit";
  id: string;
  timestamp: string;
  entry: AuditEntry;
}

export type ActivityRow = JobRow | AuditRow;

function jobTimestamp(job: ActivityJob): string {
  return job.started_at ?? job.created_at;
}

function toJobRow(job: ActivityJob): ActivityRow {
  return { kind: "job", id: `job:${job.id}`, timestamp: jobTimestamp(job), job };
}

function toAuditRow(entry: AuditEntry, index: number): ActivityRow {
  // Entries carry no id of their own; the index breaks a tie between two entries recorded in
  // the same instant (two audited effects of one request), which the timestamp alone would not.
  return { kind: "audit", id: `audit:${entry.timestamp}:${String(index)}`, timestamp: entry.timestamp, entry };
}

/** The actor exactly as the backend recorded it, or null when a job predates that field. */
export function rowActor(row: ActivityRow): string | null {
  return row.kind === "job" ? (row.job.actor ?? null) : row.entry.actor;
}

// ---------------------------------------------------------------------------------------
// Merging two paginated sources into one page.

export interface MergeInput {
  /** The jobs fetched so far, newest first - a flat "top N" list, not cursor-paginated. */
  jobs: readonly ActivityJob[];
  /** True once every job the store keeps has been fetched (`jobs.length >= total`). */
  jobsComplete: boolean;
  /** The audit entries fetched so far, newest first, across every page read. */
  entries: readonly AuditEntry[];
  /** True once the audit log's `next_before` came back null. */
  auditComplete: boolean;
  /** Narrows to one actor's rows, applied after merging so it never affects the cutoff below. */
  actor?: string | undefined;
}

export interface MergedActivity {
  rows: ActivityRow[];
  /** Whether "Load more" can still fetch something from either source. */
  hasMore: boolean;
}

function timeValue(timestamp: string | null | undefined): number {
  if (timestamp === null || timestamp === undefined || timestamp === "") return Number.NEGATIVE_INFINITY;
  return parseTimestamp(timestamp)?.getTime() ?? Number.NEGATIVE_INFINITY;
}

/**
 * Merges the jobs history and the audit log into one newest-first timeline, holding back
 * whichever tail is not yet safe to show.
 *
 * Each source is individually complete down to its own oldest fetched row: the jobs list
 * because it was asked for that many and no cursor moved, the audit log because `before` never
 * skips an entry. But the *merged* order is only trustworthy down to the shallower of the two -
 * past that point, the deeper source might hold rows in a gap the shallow one has not reached
 * yet, and showing them now would mean re-sorting already-rendered rows once it catches up.
 * Rows below that boundary are left out of this page and reappear, correctly interleaved, once
 * "Load more" extends the shallow side - never duplicated, never reordered.
 */
/** How far apart a request's generic record and the endpoint's own can be written. */
const ECHO_WINDOW_MS = 2_000;

/**
 * The rows without the request log's echoes. The server records every mutating request
 * generically (`api.post` and its status) beside whatever the endpoint itself records (a
 * sign-in attempt and why it failed); the generic one says less about the same moment, so it
 * is kept only for requests nothing else described.
 */
export function withoutRequestEchoes(rows: readonly ActivityRow[]): ActivityRow[] {
  const specific = rows.filter((row): row is AuditRow => row.kind === "audit" && !row.entry.action.startsWith("api."));
  return rows.filter((row) => {
    if (row.kind !== "audit" || !row.entry.action.startsWith("api.")) return true;
    const at = timeValue(row.timestamp);
    return !specific.some(
      (other) => other.entry.resource === row.entry.resource && Math.abs(timeValue(other.timestamp) - at) <= ECHO_WINDOW_MS,
    );
  });
}

export function mergeActivity({ jobs, jobsComplete, entries, auditComplete, actor }: MergeInput): MergedActivity {
  const lastJob = jobs.at(-1);
  const jobFloor = jobsComplete || lastJob === undefined ? Number.NEGATIVE_INFINITY : timeValue(jobTimestamp(lastJob));
  const lastEntry = entries.at(-1);
  const auditFloor = auditComplete || lastEntry === undefined ? Number.NEGATIVE_INFINITY : timeValue(lastEntry.timestamp);
  const cutoff = Math.max(jobFloor, auditFloor);

  const rows = withoutRequestEchoes([...jobs.map(toJobRow), ...entries.map(toAuditRow)])
    .filter((row) => timeValue(row.timestamp) >= cutoff)
    .filter((row) => actor === undefined || rowActor(row) === actor)
    .sort((a, b) => timeValue(b.timestamp) - timeValue(a.timestamp));

  return { rows, hasMore: !jobsComplete || !auditComplete };
}

// ---------------------------------------------------------------------------------------
// Words: what a job's type, an audit action, an audit result and an actor are called on
// screen, next to the raw value the backend actually recorded.

/** `noust.web.jobs.JobType`, in the console's words. */
const JOB_ACTION_LABELS: Readonly<Record<string, PlainKey>> = {
  deploy: "activity.jobAction.deploy",
  update: "activity.jobAction.update",
  backup: "activity.jobAction.backup",
  restore: "activity.jobAction.restore",
  rollback: "activity.jobAction.rollback",
  push: "activity.jobAction.push",
  migrate: "activity.jobAction.migrate",
  cert_create: "activity.jobAction.certCreate",
  cert_renew: "activity.jobAction.certRenew",
  service_action: "activity.jobAction.serviceAction",
  site_action: "activity.jobAction.siteAction",
  delete: "activity.jobAction.delete",
  zero_downtime: "activity.jobAction.zeroDowntime",
  custom: "activity.jobAction.custom",
};

/** A job's type, in the console's words; the raw value itself when the type is unrecognised. */
export function jobActionLabel(t: T, type: string): string {
  const key = JOB_ACTION_LABELS[type];
  return key ? t(key) : type;
}

/** The domain a job acted on, when it named one. */
export function jobResource(job: ActivityJob): string | null {
  const domain = job.metadata?.["domain"];
  return typeof domain === "string" && domain !== "" ? domain : null;
}

/**
 * Every `action` the backend audits today (`grep -rho 'action="[a-z0-9_.]*"' src/noust/web`),
 * worded so it reads correctly next to either result: an action recorded with more than one
 * result (a sign-in can succeed or fail) gets a neutral, attempt-shaped label; an action the
 * backend only ever records with one result (a lockout is always `locked`) can safely describe
 * that outcome.
 */
const AUDIT_ACTION_LABELS: Readonly<Record<string, PlainKey>> = {
  "auth.login": "activity.auditAction.authLogin",
  "auth.logout": "activity.auditAction.authLogout",
  "auth.credential": "activity.auditAction.authCredential",
  "auth.csrf": "activity.auditAction.authCsrf",
  "auth.lockout": "activity.auditAction.authLockout",
  "auth.elevate": "activity.auditAction.authElevate",
  "auth.elevation": "activity.auditAction.authElevation",
  "auth.revoke_all": "activity.auditAction.authRevokeAll",
  "auth.revoke_others": "activity.auditAction.authRevokeOthers",
  "auth.scope": "activity.auditAction.authScope",
  "auth.session.revoke": "activity.auditAction.authSessionRevoke",
  "auth.token.create": "activity.auditAction.authTokenCreate",
  "auth.token.revoke": "activity.auditAction.authTokenRevoke",
  "auth.ws_ticket": "activity.auditAction.authWsTicket",
  "auth.2fa.confirm": "activity.auditAction.auth2faConfirm",
  "auth.2fa.disable": "activity.auditAction.auth2faDisable",
  "auth.2fa.enroll": "activity.auditAction.auth2faEnroll",
  "auth.2fa.backup_codes": "activity.auditAction.auth2faBackupCodes",
  "apps.env.reveal": "activity.auditAction.appsEnvReveal",
  "apps.env.update": "activity.auditAction.appsEnvUpdate",
  "config.update": "activity.auditAction.configUpdate",
  "hooks.deploy": "activity.auditAction.hooksDeploy",
  "hooks.secret.disable": "activity.auditAction.hooksSecretDisable",
  "hooks.secret.mint": "activity.auditAction.hooksSecretMint",
  "ws.connect": "activity.auditAction.wsConnect",
};

/**
 * An audit action's words. Every mutating API call is also audited generically as
 * `api.<method>` by the security middleware (`noust.web.server`), alongside whichever specific
 * action the endpoint itself records - shown here as "POST request" and so on rather than
 * guessed at from a fixed list, since the method is the one thing about it that is always
 * known. Anything else this table does not recognise is shown verbatim - never a guess.
 */
export function auditActionLabel(t: T, action: string): string {
  if (action.startsWith("api.")) return t("activity.apiRequest", { method: action.slice("api.".length).toUpperCase() });
  const key = AUDIT_ACTION_LABELS[action];
  return key ? t(key) : action;
}

export interface ActionWords {
  label: string;
  /** The raw job type or audit action, always shown alongside the words, in mono. */
  raw: string;
}

/** A row's action, whichever source it came from. */
export function actionWords(t: T, row: ActivityRow): ActionWords {
  return row.kind === "job"
    ? { label: jobActionLabel(t, row.job.type), raw: row.job.type }
    : { label: auditActionLabel(t, row.entry.action), raw: row.entry.action };
}

/** A row's target: a job's domain, or an audit entry's resource. */
export function resourceOf(row: ActivityRow): string | null {
  return row.kind === "job" ? jobResource(row.job) : (row.entry.resource ?? null);
}

/** A row's free-text context: a job's description, or an audit entry's detail. */
export function detailOf(row: ActivityRow): string | null {
  if (row.kind === "job") return row.job.description || row.job.name || null;
  return row.entry.detail ?? null;
}

function capitalise(text: string): string {
  return text.length === 0 ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

/** `result` values the audit log writes (`grep -rho 'result="[a-z]*"' src/noust/web`), as keys. */
const AUDIT_RESULT_STATUS: Readonly<Record<string, { state: StatusView["state"]; key: PlainKey; attention: boolean }>> = {
  success: { state: "running", key: "activity.auditResult.success", attention: false },
  ok: { state: "running", key: "activity.auditResult.ok", attention: false },
  denied: { state: "failed", key: "activity.auditResult.denied", attention: true },
  failure: { state: "failed", key: "activity.auditResult.failure", attention: true },
  locked: { state: "failed", key: "activity.auditResult.locked", attention: true },
};

/** Maps an audit entry's result to the same state language as an app's or a job's status. */
export function auditResultStatus(t: T, result: string): StatusView {
  const word = result.trim();
  if (word === "") return { state: "unknown", label: t("activity.auditResult.unknown"), attention: false };
  const known = AUDIT_RESULT_STATUS[word.toLowerCase()];
  if (known !== undefined) return { state: known.state, label: t(known.key), attention: known.attention };
  // The security middleware's generic per-request entry (`noust.web.server`) writes
  // `error:<status>` rather than one of the fixed words above; it is still a failure.
  if (word.toLowerCase().startsWith("error")) {
    const colon = word.indexOf(":");
    const code = colon === -1 ? "" : word.slice(colon + 1);
    return { state: "failed", label: code === "" ? t("activity.auditResult.error") : t("activity.auditResult.errorCode", { code }), attention: true };
  }
  return { state: "unknown", label: capitalise(word.replace(/_/g, " ")), attention: false };
}

/** A row's result, whichever source it came from, in the app/deploy/job state language. */
export function resultView(t: T, row: ActivityRow): StatusView {
  return row.kind === "job" ? deployStatus(row.job.status, t.locale) : auditResultStatus(t, row.entry.result);
}

export interface ActorWords {
  /** What to show as the main text. */
  label: string;
  /** The exact value the backend recorded - always shown too, in mono. */
  raw: string;
}

/**
 * `actor_label()` in `noust.web.auth`: `"master"`, `"token:<name>"`, a browser session's id
 * (the full value, or the twelve characters that helper keeps), `"webhook"` for a deploy the
 * repository's own hook triggered, or `"anonymous"` for an unauthenticated attempt. Every case
 * keeps the raw value alongside the words - the exact string a filter or a support request
 * needs is never hidden behind the paraphrase.
 */
export function describeActor(t: T, actor: string): ActorWords {
  if (actor === "master") return { label: t("activity.actor.masterToken"), raw: actor };
  if (actor === "anonymous") return { label: t("activity.actor.anonymous"), raw: actor };
  if (actor === "webhook") return { label: t("activity.actor.webhookDelivery"), raw: actor };
  if (actor.startsWith("token:")) {
    const name = actor.slice("token:".length);
    return { label: name === "" ? t("activity.actor.apiToken") : t("activity.actor.namedToken", { name }), raw: actor };
  }
  const short = actor.slice(0, 8);
  return { label: short === "" ? t("activity.actor.browserSession") : t("activity.actor.session", { short }), raw: actor };
}

/** A row's actor, worded - "Not recorded" for a job queued before jobs carried one. */
export function actorWords(t: T, row: ActivityRow): ActorWords {
  const raw = rowActor(row);
  return raw === null ? { label: t("activity.actor.notRecorded"), raw: "-" } : describeActor(t, raw);
}

// ---------------------------------------------------------------------------------------
// Filters, in the URL.

/** `JobStatus`. */
export const JOB_STATUSES: ReadonlySet<string> = new Set(["pending", "running", "completed", "failed", "cancelled"]);
/** The `result` values the audit log writes. */
export const AUDIT_RESULTS: ReadonlySet<string> = new Set(["success", "ok", "denied", "failure", "locked"]);

const JOB_RESULT_WORDS: Readonly<Record<string, PlainKey>> = {
  completed: "activity.jobStatus.completed",
  failed: "activity.jobStatus.failed",
  cancelled: "activity.jobStatus.cancelled",
  pending: "activity.jobStatus.pending",
  running: "activity.jobStatus.running",
};

const AUDIT_RESULT_WORDS: Readonly<Record<string, PlainKey>> = {
  success: "activity.auditResult.success",
  ok: "activity.auditResult.ok",
  denied: "activity.auditResult.denied",
  failure: "activity.auditResult.failure",
  locked: "activity.auditResult.locked",
};

export interface ActivitySearch {
  kind?: "jobs" | "audit";
  /** A job status or an audit result, whichever `kind` allows. */
  result?: string;
  /** The exact actor value: `master`, `token:<name>`, `webhook`, `anonymous` or a session id. */
  actor?: string;
}

/** Whether a result value means anything under a kind: a job status when jobs are shown, an audit result when audit entries are. */
export function resultValidFor(result: string, kind: ActivitySearch["kind"]): boolean {
  if (kind === "jobs") return JOB_STATUSES.has(result);
  if (kind === "audit") return AUDIT_RESULTS.has(result);
  return JOB_STATUSES.has(result) || AUDIT_RESULTS.has(result);
}

export interface ResultOption {
  value: string;
  label: string;
}

/**
 * The Result filter's options for a kind: both vocabularies when everything is shown (prefixed
 * so "Failed" the job status and "Failed" the audit result are not offered as one confusing
 * entry), just the relevant one once `kind` narrows it.
 */
export function resultOptions(t: T, kind: ActivitySearch["kind"]): ResultOption[] {
  const both = kind === undefined;
  const jobs =
    kind !== "audit"
      ? Object.entries(JOB_RESULT_WORDS).map(([value, key]) => ({
          value,
          label: both ? t("activity.jobResultPrefix", { label: t(key) }) : t(key),
        }))
      : [];
  const audit =
    kind !== "jobs"
      ? Object.entries(AUDIT_RESULT_WORDS).map(([value, key]) => ({
          value,
          label: both ? t("activity.actionResultPrefix", { label: t(key) }) : t(key),
        }))
      : [];
  return [...jobs, ...audit];
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

export function validateActivitySearch(search: Record<string, unknown>): ActivitySearch {
  const kindRaw = text(search["kind"]);
  const kind = kindRaw === "jobs" || kindRaw === "audit" ? kindRaw : undefined;
  const resultRaw = text(search["result"]);
  const result = resultRaw !== undefined && resultValidFor(resultRaw, kind) ? resultRaw : undefined;
  const actor = text(search["actor"]);
  return {
    ...(kind !== undefined ? { kind } : {}),
    ...(result !== undefined ? { result } : {}),
    ...(actor !== undefined ? { actor } : {}),
  };
}

export function isFiltered(search: ActivitySearch): boolean {
  return search.kind !== undefined || search.result !== undefined || search.actor !== undefined;
}
