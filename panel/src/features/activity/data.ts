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
import { auditEvents } from "../../i18n/en/auditEvents";
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
  /** A sign-in that went through the second factor: the "asked for the code" step before it is folded into it. */
  secondFactor?: boolean;
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

/** How long after asking for the second factor a sign-in from the same address still answers it. */
const SECOND_FACTOR_WINDOW_MS = 10 * 60_000;

/** A sign-in that stopped to ask for the second factor: the first half of a normal two-factor sign-in. */
export function isSecondFactorAsk(entry: AuditEntry): boolean {
  return entry.action === "auth.login" && entry.result !== "success" && entry.result !== "ok" && /second factor/i.test(entry.detail ?? "");
}

function succeeded(entry: AuditEntry): boolean {
  return entry.result === "success" || entry.result === "ok";
}

/**
 * One row per two-factor sign-in, not two: the "second factor required" step is dropped when
 * a sign-in from the same address succeeded within minutes of it, and that sign-in says it
 * went through the second factor. An ask nobody answered stays, as what it was.
 */
export function foldSecondFactor(rows: readonly ActivityRow[]): ActivityRow[] {
  const asks = rows.filter((row): row is AuditRow => row.kind === "audit" && isSecondFactorAsk(row.entry));
  if (asks.length === 0) return [...rows];
  const signIns = rows.filter((row): row is AuditRow => row.kind === "audit" && row.entry.action === "auth.login" && succeeded(row.entry));
  const answered = new Set<string>();
  const confirmed = new Set<string>();
  for (const ask of asks) {
    const at = timeValue(ask.timestamp);
    const match = signIns.find(
      (row) =>
        !confirmed.has(row.id) &&
        (row.entry.client_ip ?? null) === (ask.entry.client_ip ?? null) &&
        timeValue(row.timestamp) >= at &&
        timeValue(row.timestamp) - at <= SECOND_FACTOR_WINDOW_MS,
    );
    if (match === undefined) continue;
    answered.add(ask.id);
    confirmed.add(match.id);
  }
  return rows.filter((row) => !answered.has(row.id)).map((row) => (row.kind === "audit" && confirmed.has(row.id) ? { ...row, secondFactor: true } : row));
}

export function mergeActivity({ jobs, jobsComplete, entries, auditComplete, actor }: MergeInput): MergedActivity {
  const lastJob = jobs.at(-1);
  const jobFloor = jobsComplete || lastJob === undefined ? Number.NEGATIVE_INFINITY : timeValue(jobTimestamp(lastJob));
  const lastEntry = entries.at(-1);
  const auditFloor = auditComplete || lastEntry === undefined ? Number.NEGATIVE_INFINITY : timeValue(lastEntry.timestamp);
  const cutoff = Math.max(jobFloor, auditFloor);

  const rows = foldSecondFactor(withoutRequestEchoes([...jobs.map(toJobRow), ...entries.map(toAuditRow)]))
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

/**
 * An audit action's words. Every mutating API call is also audited generically as
 * `api.<method>` by the security middleware (`noust.web.server`), alongside whichever specific
 * action the endpoint itself records - shown here as "POST request" and so on rather than
 * guessed at from a fixed list, since the method is the one thing about it that is always
 * known. Anything else this table does not recognise is shown verbatim - never a guess.
 */
export function auditActionLabel(t: T, action: string): string {
  if (action.startsWith("api.")) return t("activity.apiRequest", { method: action.slice("api.".length).toUpperCase() });
  const key = action.replaceAll(".", "_");
  // The whole closed catalog (src/noust/core/audit/catalog.py), in the typed catalogs.
  return Object.hasOwn(auditEvents.action, key) ? t(`auditEvents.action.${key}` as PlainKey) : action;
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

/** API paths whose next segment names the thing acted on: an app, a site, a certificate... */
const OBJECT_PATHS = /^\/api\/(?:apps|sites|certs|cron|services|backups|backup-destinations|backup-schedules|nodes|previews)\/([^/?#]+)/;
/** Segments under those paths that are endpoints of the collection, not one of its objects. */
const NOT_OBJECTS: ReadonlySet<string> = new Set(["preview", "templates", "backends", "reload", "renew-all", "storage", "import"]);
const DATABASE_PATH = /^\/api\/databases\/[^/]+\/([^/?#]+)/;

/**
 * What an action was done to, as the operator names it: `shop.example.com` for
 * `/api/apps/shop.example.com/restart`, a database's name, a site's. A path that names no
 * object (a sign-in's `/api/auth/login`: the console itself) has none; any other resource is
 * shown as recorded. The raw value always stays in reach, in the title.
 */
export function resourceWords(row: ActivityRow): { object: string | null; raw: string | null } {
  const raw = resourceOf(row);
  if (row.kind === "job" || raw === null) return { object: raw, raw };
  const object = OBJECT_PATHS.exec(raw) ?? DATABASE_PATH.exec(raw);
  if (object?.[1] !== undefined && !NOT_OBJECTS.has(object[1])) {
    try {
      return { object: decodeURIComponent(object[1]), raw };
    } catch {
      return { object: object[1], raw };
    }
  }
  if (raw.startsWith("/api/auth") || raw.startsWith("/ws/") || raw.startsWith("/events")) return { object: null, raw };
  return { object: raw, raw };
}

/** A row's free-text context: a job's description, or an audit entry's detail. */
export function detailOf(row: ActivityRow): string | null {
  if (row.kind === "job") {
    // A job the console names (its type, its application) says nothing more in the server's
    // English description; only a type it does not know keeps the server's words.
    if (JOB_ACTION_LABELS[row.job.type] !== undefined) return null;
    return row.job.description || row.job.name || null;
  }
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
  warning: { state: "warning", key: "activity.auditResult.warning", attention: true },
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
  if (row.kind === "job") return deployStatus(row.job.status, t.locale);
  // Not a failure: the first step of every two-factor sign-in, left alone when nobody answered it.
  if (isSecondFactorAsk(row.entry)) return { state: "stopped", label: t("activity.auditResult.askedSecondFactor"), attention: false };
  return auditResultStatus(t, row.entry.result);
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

type AuditActor = NonNullable<AuditEntry["who"]>;

/**
 * An actor the 3.1 audit log describes in full (`who`): an account by its name, a token by
 * its own, a command at the terminal by the login that typed it, an operator acting through a
 * central by both. The recorded label stays the raw value.
 */
export function describeWho(t: T, who: AuditActor, raw: string): ActorWords {
  const name = who.name ?? who.id ?? null;
  switch (who.kind) {
    case "master":
      return { label: t("activity.actor.masterToken"), raw };
    case "anonymous":
      return { label: t("activity.actor.anonymous"), raw };
    case "token": {
      const token = name?.replace(/^token:/, "") ?? "";
      return { label: token === "" ? t("activity.actor.apiToken") : t("activity.actor.namedToken", { name: token }), raw };
    }
    case "cli":
      return { label: name === null ? t("activity.actor.terminalUnknown") : t("activity.actor.terminal", { name }), raw };
    case "fleet": {
      const central = who.via?.replace(/^fleet:/, "") ?? null;
      if (name !== null && central !== null) return { label: t("activity.actor.throughCentral", { name, central }), raw };
      return { label: name ?? central ?? raw, raw };
    }
    case "system":
      return { label: t("activity.actor.system"), raw };
    default:
      return { label: name ?? raw, raw };
  }
}

/** A row's actor, worded - "Not recorded" for a job queued before jobs carried one. */
export function actorWords(t: T, row: ActivityRow): ActorWords {
  if (row.kind === "audit" && row.entry.who !== null && row.entry.who !== undefined) return describeWho(t, row.entry.who, row.entry.actor);
  const raw = rowActor(row);
  return raw === null ? { label: t("activity.actor.notRecorded"), raw: "-" } : describeActor(t, raw);
}

/** Where an audited action came from: the address or terminal the actor used, when recorded. */
export function actorSource(row: ActivityRow): string | null {
  if (row.kind !== "audit") return null;
  return row.entry.who?.source ?? row.entry.client_ip ?? null;
}

// ---------------------------------------------------------------------------------------
// Which rows a view shows: operations (what was done to the server and its applications),
// sign-ins and access, or everything.

/** Categories of the audit catalog (`noust.core.audit.catalog`) that change something. */
const OPERATION_CATEGORIES: ReadonlySet<string> = new Set(["change", "config", "account"]);
/** Categories about who got in, who was refused, and who saw a secret. */
const ACCESS_CATEGORIES: ReadonlySet<string> = new Set(["access", "denial", "read"]);

/**
 * An audit entry's category: the catalog's own since 3.1, which the server also fills in for
 * older lines since 3.1.1 (`category_of` in `noust.core.audit.log`); from a node older than
 * that, a line with none is filed the same way here: the sign-in family (every `auth.*` and
 * socket event) is access, the rest changed something.
 */
export function auditCategory(entry: AuditEntry): string {
  if (entry.category !== null && entry.category !== undefined && entry.category !== "") return entry.category;
  if (entry.action.startsWith("auth.") || entry.action.startsWith("ws.")) return "access";
  return "change";
}

/**
 * The audit categories a view reads, asked of the server (`GET /api/audit?categories=`) so a
 * page of the log is a page of the view: filtered here after reading, the newest page could be
 * all sign-ins, leaving the view empty and every job older than them behind "Load more". The
 * server also leaves out a request's generic line when its request recorded an event of its
 * own, filed in its own view. Undefined for everything. A node older than 3.1.1 ignores the
 * parameter, which is why `inKind` still narrows what is shown.
 */
export function auditCategoriesFor(kind: ActivitySearch["kind"]): string[] | undefined {
  if (kind === "all") return undefined;
  return [...(kind === "access" ? ACCESS_CATEGORIES : OPERATION_CATEGORIES)];
}

/** Whether a row belongs to a view: jobs are operations; audit entries go by their category. */
export function inKind(row: ActivityRow, kind: ActivitySearch["kind"]): boolean {
  if (kind === "all") return true;
  if (row.kind === "job") return kind === undefined;
  const category = auditCategory(row.entry);
  return kind === "access" ? ACCESS_CATEGORIES.has(category) : OPERATION_CATEGORIES.has(category);
}

/** Whether a row matches free text, against what it shows and what the backend recorded. */
export function matchesText(row: ActivityRow, text: string, words: readonly string[] = []): boolean {
  const needle = text.trim().toLowerCase();
  if (needle === "") return true;
  const recorded =
    row.kind === "job"
      ? [row.job.name, row.job.description, row.job.type, row.job.actor ?? "", jobResource(row.job) ?? ""]
      : [row.entry.action, row.entry.actor, row.entry.resource ?? "", row.entry.detail ?? "", row.entry.client_ip ?? "", row.entry.who?.name ?? ""];
  return [...recorded, ...words].some((value) => value.toLowerCase().includes(needle));
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
  /** The view: operations when absent, sign-ins and access, or everything. */
  kind?: "access" | "all";
  /** A job status or an audit result, whichever the view allows. */
  result?: string;
  /** Free text, matched against what each row shows and what the backend recorded. */
  q?: string;
  /** The exact actor value: `master`, `token:<name>`, `webhook`, `anonymous` or a session id. */
  actor?: string;
}

/** Whether a result value means anything in a view: sign-ins have no jobs, so no job status. */
export function resultValidFor(result: string, kind: ActivitySearch["kind"]): boolean {
  if (kind === "access") return AUDIT_RESULTS.has(result);
  return JOB_STATUSES.has(result) || AUDIT_RESULTS.has(result);
}

export interface ResultOption {
  value: string;
  label: string;
}

/**
 * The Result filter's options for a view: both vocabularies where jobs and audited actions mix
 * (prefixed, so "Failed" the job status and "Failed" the audit result are not offered as one
 * confusing entry), the audit's alone for sign-ins.
 */
export function resultOptions(t: T, kind: ActivitySearch["kind"]): ResultOption[] {
  const both = kind !== "access";
  const jobs = both
    ? Object.entries(JOB_RESULT_WORDS).map(([value, key]) => ({ value, label: t("activity.jobResultPrefix", { label: t(key) }) }))
    : [];
  const audit = Object.entries(AUDIT_RESULT_WORDS).map(([value, key]) => ({
    value,
    label: both ? t("activity.actionResultPrefix", { label: t(key) }) : t(key),
  }));
  return [...jobs, ...audit];
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

/**
 * The page's search params. 3.0's `kind=jobs` and `kind=audit` still open: jobs are the
 * operations view now, and the audit log alone is everything.
 */
export function validateActivitySearch(search: Record<string, unknown>): ActivitySearch {
  const kindRaw = text(search["kind"]);
  const kind = kindRaw === "access" || kindRaw === "all" ? kindRaw : kindRaw === "audit" ? "all" : undefined;
  const resultRaw = text(search["result"]);
  const result = resultRaw !== undefined && resultValidFor(resultRaw, kind) ? resultRaw : undefined;
  const q = text(search["q"]);
  const actor = text(search["actor"]);
  return {
    ...(kind !== undefined ? { kind } : {}),
    ...(result !== undefined ? { result } : {}),
    ...(q !== undefined ? { q } : {}),
    ...(actor !== undefined ? { actor } : {}),
  };
}

export function isFiltered(search: ActivitySearch): boolean {
  return search.kind !== undefined || search.result !== undefined || search.q !== undefined || search.actor !== undefined;
}
