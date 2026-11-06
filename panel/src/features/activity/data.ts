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
import { serverSentence } from "../../components/ui/ServerText";
import { hasMessage } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { parseTimestamp } from "../../lib/format";
import type { RowOrigin } from "../fleet/data";
import type { ActivitySearch } from "./search";

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
  /** The server it happened on, when the list holds more than one. */
  origin?: RowOrigin;
}

export type ActivityRow = JobRow | AuditRow;

function jobTimestamp(job: ActivityJob): string {
  return job.started_at ?? job.created_at;
}

function toJobRow(job: ActivityJob): ActivityRow {
  return { kind: "job", id: `job:${job.id}`, timestamp: jobTimestamp(job), job };
}

function toAuditRow(entry: AuditEntry, index: number, origin?: RowOrigin): ActivityRow {
  // Entries carry no id of their own; the index breaks a tie between two entries recorded in
  // the same instant (two audited effects of one request), which the timestamp alone would not.
  const row: AuditRow = { kind: "audit", id: `audit:${origin?.node ?? ""}:${entry.timestamp}:${String(index)}`, timestamp: entry.timestamp, entry };
  return origin === undefined ? row : { ...row, origin };
}

/**
 * The events of several servers as rows of one timeline: each with the server it happened on. A
 * server's own events are what it recorded; the console reads them as it reads one server's.
 */
export function auditRowsOf(items: readonly { entry: AuditEntry; origin: RowOrigin }[]): ActivityRow[] {
  return items.map((item, index) => toAuditRow(item.entry, index, item.origin));
}

/** The actor exactly as the backend recorded it, or null when a job predates that field. */
export function rowActor(row: ActivityRow): string | null {
  return row.kind === "job" ? (row.job.actor ?? null) : row.entry.actor;
}

/** The accounts Noust makes for its own work (the fleet's sync): somebody to name in a log, not a person to ask about. */
const INTERNAL_ACCOUNT_PREFIX = "noust-";

/**
 * The person behind an event: the account's name, the same whether they acted in the console of the
 * server itself or through a central (`marta` and `fleet-fra-1 on behalf of marta` are one person).
 * A token, a terminal, a webhook and Noust itself are not people, and have none.
 */
export function personOf(entry: AuditEntry): string | null {
  const who = entry.who;
  if (who !== null && who !== undefined) {
    const named = (who.kind === "user" || who.kind === "fleet") && who.name !== null && who.name !== undefined && who.name !== "" ? who.name : null;
    return named === null || named.startsWith(INTERNAL_ACCOUNT_PREFIX) ? null : named;
  }
  return fleetActor(entry.actor)?.person ?? null;
}

/** Whether a row was done by this person: an audit entry by its account, a job by the label it was queued under. */
export function doneBy(row: ActivityRow, person: string): boolean {
  if (row.kind === "audit") return personOf(row.entry) === person;
  const actor = row.job.actor ?? null;
  return actor !== null && (actor === person || fleetActor(actor)?.person === person);
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
  /** Narrows to what one person did, by their account's name, in the console or through a central. */
  person?: string | undefined;
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
      (other) =>
        (other.origin?.node ?? "") === (row.origin?.node ?? "") &&
        other.entry.resource === row.entry.resource &&
        Math.abs(timeValue(other.timestamp) - at) <= ECHO_WINDOW_MS,
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
        (row.origin?.node ?? "") === (ask.origin?.node ?? "") &&
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

export function mergeActivity({ jobs, jobsComplete, entries, auditComplete, actor, person }: MergeInput): MergedActivity {
  const lastJob = jobs.at(-1);
  const jobFloor = jobsComplete || lastJob === undefined ? Number.NEGATIVE_INFINITY : timeValue(jobTimestamp(lastJob));
  const lastEntry = entries.at(-1);
  const auditFloor = auditComplete || lastEntry === undefined ? Number.NEGATIVE_INFINITY : timeValue(lastEntry.timestamp);
  const cutoff = Math.max(jobFloor, auditFloor);

  const rows = foldSecondFactor(withoutRequestEchoes([...jobs.map(toJobRow), ...entries.map((entry, index) => toAuditRow(entry, index))]))
    .filter((row) => timeValue(row.timestamp) >= cutoff)
    .filter((row) => actor === undefined || rowActor(row) === actor)
    .filter((row) => person === undefined || doneBy(row, person))
    .sort((a, b) => timeValue(b.timestamp) - timeValue(a.timestamp));

  return { rows, hasMore: !jobsComplete || !auditComplete };
}

/**
 * The events of every server as one newest-first timeline. Each server answers its newest events
 * and the page asks for more of all of them together, so the order is the same whatever the
 * servers' number; only the narrowing to a person is left to do.
 */
export function fleetTimeline(items: readonly { entry: AuditEntry; origin: RowOrigin }[], person?: string, actor?: string): ActivityRow[] {
  return foldSecondFactor(withoutRequestEchoes(auditRowsOf(items)))
    .filter((row) => actor === undefined || rowActor(row) === actor)
    .filter((row) => person === undefined || doneBy(row, person))
    .sort((a, b) => timeValue(b.timestamp) - timeValue(a.timestamp));
}

/** The people who appear in a list of rows, by name: the choices of the person filter. */
export function peopleIn(rows: readonly ActivityRow[]): string[] {
  const names = new Set<string>();
  for (const row of rows) {
    const person = row.kind === "audit" ? personOf(row.entry) : fleetActor(row.job.actor ?? "")?.person ?? null;
    if (person !== null) names.add(person);
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

// ---------------------------------------------------------------------------------------
// Words: what a job's type, an audit action, an audit result and an actor are called on
// screen, next to the raw value the backend actually recorded.

/** `noust.web.jobs.JobType`, in the console's words. */
const JOB_ACTION_LABELS: Readonly<Record<string, PlainKey>> = {
  deploy: "activity.jobAction.deploy",
  update: "common.words.update",
  backup: "activity.jobAction.backup",
  restore: "activity.jobAction.restore",
  rollback: "activity.jobAction.rollback",
  push: "activity.jobAction.push",
  migrate: "activity.jobAction.migrate",
  cert_create: "activity.jobAction.certCreate",
  cert_renew: "activity.jobAction.certRenew",
  service_action: "activity.jobAction.serviceAction",
  site_action: "activity.jobAction.siteAction",
  delete: "common.words.delete",
  zero_downtime: "activity.jobAction.zeroDowntime",
  app_exec: "activity.jobAction.appExec",
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
  return hasMessage(`auditEvents.action.${key}`) ? t(`auditEvents.action.${key}` as PlainKey) : action;
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
  success: { state: "running", key: "common.deployState.succeeded", attention: false },
  ok: { state: "running", key: "common.deployState.succeeded", attention: false },
  denied: { state: "failed", key: "activity.auditResult.denied", attention: true },
  failure: { state: "failed", key: "common.statusPill.failed", attention: true },
  locked: { state: "failed", key: "activity.auditResult.locked", attention: true },
  warning: { state: "warning", key: "common.statusPill.warning", attention: true },
};

/** Maps an audit entry's result to the same state language as an app's or a job's status. */
export function auditResultStatus(t: T, result: string): StatusView {
  const word = result.trim();
  if (word === "") return { state: "unknown", label: t("common.statusPill.unknown"), attention: false };
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

/** The central a fleet token is named after: `fleet-fra-1` and `fleet-fra-1.2` are `fra-1`; any other name is its own. */
function centralOf(token: string): string {
  const name = token.replace(/^fleet:/, "");
  return /^fleet-([a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)(?:\.[0-9]+)?$/.exec(name)?.[1] ?? name;
}

/** The person and the central of a label like `fleet-fra-1 on behalf of marta` (`actor_label()`); null for any other actor. */
export function fleetActor(label: string): { person: string; central: string } | null {
  const [token, ...rest] = label.split(" on behalf of ");
  const person = rest.join(" on behalf of ").trim();
  return rest.length > 0 && token !== undefined && token.trim() !== "" && person !== "" ? { person, central: centralOf(token.trim()) } : null;
}

/**
 * A person who acted through a central, named first and in the console's language: `marta (from
 * the central fra-1)`. It is the server's sentence (`actors.through_central`), so the Spanish one is
 * the same everywhere an actor is worded.
 */
function throughCentral(t: T, person: string, central: string): string {
  return serverSentence(t.locale, { code: "actors.through_central", params: { person, central } }, `${person} (from the central ${central})`, "text");
}

/**
 * Who acted, for a place that shows a stored label as it is ("changed by", "taken by"): the person
 * behind a central first and in the console's language, and every other label untouched - a name
 * is a name, and what a person typed is not worded.
 */
export function namedActor(t: T, label: string): string {
  const fleet = fleetActor(label);
  return fleet === null ? label : throughCentral(t, fleet.person, fleet.central);
}

/** The labels the server gives what it does by itself (`AUTOMATIC_ACTOR` of the fleet's settings and of the sandbox trial). */
const AUTOMATIC_ACTORS: ReadonlySet<string> = new Set(["noust", "noust-fleet-sync"]);

/**
 * `actor_label()` in `noust.web.auth`: `"master"`, `"token:<name>"`, a browser session's id
 * (the full value, or the twelve characters that helper keeps), `"webhook"` for a deploy the
 * repository's own hook triggered, or `"anonymous"` for an unauthenticated attempt. Every case
 * keeps the raw value alongside the words - the exact string a filter or a support request
 * needs is never hidden behind the paraphrase.
 */
export function describeActor(t: T, actor: string): ActorWords {
  const fleet = fleetActor(actor);
  if (fleet !== null) return { label: throughCentral(t, fleet.person, fleet.central), raw: actor };
  if (actor === "master") return { label: t("activity.actor.masterToken"), raw: actor };
  if (actor === "anonymous") return { label: t("activity.actor.anonymous"), raw: actor };
  if (actor === "webhook") return { label: t("activity.actor.webhookDelivery"), raw: actor };
  // What Noust does on its own: the label a job carries when nobody asked (the fleet's settings brought to a server that
  // just joined, the sandbox trial of an update), which is not the first characters of a session.
  if (AUTOMATIC_ACTORS.has(actor) || actor === "system" || actor.startsWith("system:")) return { label: t("activity.actor.system"), raw: actor };
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
      const central = who.via !== undefined && who.via !== null ? centralOf(who.via) : null;
      if (name !== null && central !== null) return { label: throughCentral(t, name, central), raw };
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

/** The audit events that put a new version in front of people (`noust.core.audit.catalog`). */
const DEPLOYMENT_ACTIONS: ReadonlySet<string> = new Set([
  "apps.create",
  "apps.deploy",
  "apps.update",
  "apps.rollback",
  "apps.migrate",
  "hooks.deploy",
  "deploys.scheduled",
  "deploys.frozen_override",
]);

/** The jobs that do the same (`noust.web.jobs.JobType`). */
const DEPLOYMENT_JOBS: ReadonlySet<string> = new Set(["deploy", "update", "rollback", "push", "migrate", "zero_downtime"]);

/** Whether an audit event is a deployment. */
export function isDeployment(entry: AuditEntry): boolean {
  return DEPLOYMENT_ACTIONS.has(entry.action);
}

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
  if (kind === "deployments") return row.kind === "job" ? DEPLOYMENT_JOBS.has(row.job.type) : isDeployment(row.entry);
  if (row.kind === "job") return kind === undefined;
  const category = auditCategory(row.entry);
  return kind === "access" ? ACCESS_CATEGORIES.has(category) : OPERATION_CATEGORIES.has(category);
}

/** Whether a row matches free text, against what it shows and what the backend recorded. */
export function matchesText(row: ActivityRow, text: string, words: readonly string[] = []): boolean {
  const needle = text.trim().toLowerCase();
  if (needle === "") return true;
  // A job is found by its own id too: "Open on a server" from a fleet action puts the id of the job that server ran in the search.
  const recorded =
    row.kind === "job"
      ? [row.job.id, row.job.name, row.job.description, row.job.type, row.job.actor ?? "", jobResource(row.job) ?? ""]
      : [row.entry.action, row.entry.actor, row.entry.resource ?? "", row.entry.detail ?? "", row.entry.client_ip ?? "", row.entry.who?.name ?? ""];
  return [...recorded, ...words].some((value) => value.toLowerCase().includes(needle));
}

// ---------------------------------------------------------------------------------------
// Filters, in the URL.

const JOB_RESULT_WORDS: Readonly<Record<string, PlainKey>> = {
  completed: "activity.jobStatus.completed",
  failed: "common.words.failedFeminine",
  cancelled: "activity.jobStatus.cancelled",
  pending: "activity.jobStatus.pending",
  running: "common.deployState.inProgress",
};

const AUDIT_RESULT_WORDS: Readonly<Record<string, PlainKey>> = {
  success: "common.deployState.succeeded",
  ok: "common.deployState.succeeded",
  denied: "activity.auditResult.denied",
  failure: "common.statusPill.failed",
  locked: "activity.auditResult.locked",
};

export interface ResultOption {
  value: string;
  label: string;
}

/**
 * The Result filter's options for a view: both vocabularies where jobs and audited actions mix
 * (prefixed, so "Failed" the job status and "Failed" the audit result are not offered as one
 * confusing entry), the audit's alone for sign-ins.
 */
export function resultOptions(t: T, kind: ActivitySearch["kind"], withJobs = true): ResultOption[] {
  const both = kind !== "access" && withJobs;
  const jobs = both
    ? Object.entries(JOB_RESULT_WORDS).map(([value, key]) => ({ value, label: t("activity.jobResultPrefix", { label: t(key) }) }))
    : [];
  const audit = Object.entries(AUDIT_RESULT_WORDS)
    // Where there are no jobs the audit's words are alone, and "ok" is "success" said by an older server: one option for the one word.
    .filter(([value]) => withJobs || value !== "ok")
    .map(([value, key]) => ({
      value,
      label: both ? t("activity.actionResultPrefix", { label: t(key) }) : t(key),
    }));
  return [...jobs, ...audit];
}

/**
 * Whether a row has the result chosen: an audit entry by the `result` it recorded (`ok` is what an older server
 * wrote for `success`), a job by its status. No result chosen keeps every row. A list that reads only the audit
 * logs (every server at once) narrows with this after reading, because the central's view has no result to ask for.
 */
export function matchesResult(row: ActivityRow, result: string | undefined): boolean {
  if (result === undefined) return true;
  if (row.kind === "job") return row.job.status === result;
  const recorded = row.entry.result;
  const succeeded = recorded === "success" || recorded === "ok";
  return recorded === result || (succeeded && (result === "success" || result === "ok"));
}
