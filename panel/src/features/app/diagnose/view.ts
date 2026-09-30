/**
 * How a diagnosis is drawn: the words and tones for its verdict and for each check, from the
 * vocabulary of `noust.managers.diagnose` (verdicts `healthy`, `degraded`, `down`; checks `ok`,
 * `warn`, `fail`, `skip`). A word the console does not know is still shown, verbatim, in the
 * neutral tone: the backend's word beats a guess.
 */

import type { Diagnosis } from "../../../api/queries/apps";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n";

export type Tone = "ok" | "warn" | "fail" | "idle";

export type Check = Diagnosis["checks"][number];

export interface StatusWord {
  tone: Tone;
  /** The word on screen, next to the shape: never the colour alone. */
  word: string;
}

function capitalise(text: string): string {
  const clean = text.trim().replace(/_/g, " ");
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

/** The verdict as the console draws it. */
export function verdictView(verdict: string, locale: Locale = getLocale()): StatusWord {
  switch (verdict.trim().toLowerCase()) {
    case "healthy":
      return { tone: "ok", word: translate(locale, "appPages.diagnose.verdict.healthy") };
    case "degraded":
      return { tone: "warn", word: translate(locale, "appPages.diagnose.verdict.degraded") };
    case "down":
      return { tone: "fail", word: translate(locale, "appPages.diagnose.verdict.down") };
    default:
      return { tone: "idle", word: capitalise(verdict) || "Unknown" };
  }
}

/** One check's status as the console draws it. */
export function checkStatus(status: string, locale: Locale = getLocale()): StatusWord {
  switch (status.trim().toLowerCase()) {
    case "ok":
      return { tone: "ok", word: translate(locale, "appPages.diagnose.status.ok") };
    case "warn":
      return { tone: "warn", word: translate(locale, "appPages.diagnose.status.warn") };
    case "fail":
      return { tone: "fail", word: translate(locale, "appPages.diagnose.status.fail") };
    case "skip":
      return { tone: "idle", word: translate(locale, "appPages.diagnose.status.skip") };
    default:
      return { tone: "idle", word: capitalise(status) || "Unknown" };
  }
}

/** What each probe looks at, in the interface's words. */
export function checkLabel(name: string, locale: Locale = getLocale()): string {
  switch (name) {
    case "unit":
      return translate(locale, "appPages.diagnose.check.unit");
    case "port":
      return translate(locale, "appPages.diagnose.check.port");
    case "http_direct":
      return translate(locale, "appPages.diagnose.check.httpDirect");
    case "http_nginx":
      return translate(locale, "appPages.diagnose.check.httpNginx");
    case "journal":
      return translate(locale, "appPages.diagnose.check.journal");
    case "nginx_log":
      return translate(locale, "appPages.diagnose.check.nginxLog");
    case "certificate":
      return translate(locale, "appPages.diagnose.check.certificate");
    case "last_deployment":
      return translate(locale, "appPages.diagnose.check.lastDeployment");
    case "oom":
      return translate(locale, "appPages.diagnose.check.oom");
    case "disk":
      return translate(locale, "appPages.diagnose.check.disk");
    default:
      return capitalise(name);
  }
}

/** The sentence under the verdict when the checks name no single cause. */
export function causeFallback(verdict: string, locale: Locale = getLocale()): string {
  switch (verdict.trim().toLowerCase()) {
    case "healthy":
      return translate(locale, "appPages.diagnose.causeFallback.healthy");
    case "down":
      return translate(locale, "appPages.diagnose.causeFallback.down");
    default:
      return translate(locale, "appPages.diagnose.causeFallback.default");
  }
}

export interface Tally {
  ok: number;
  warn: number;
  fail: number;
  skip: number;
}

/** How many checks ended in each status; statuses the console does not know count as skipped. */
export function tally(checks: readonly Check[]): Tally {
  const counts: Tally = { ok: 0, warn: 0, fail: 0, skip: 0 };
  for (const check of checks) {
    const key = check.status.trim().toLowerCase();
    if (key === "ok" || key === "warn" || key === "fail") counts[key] += 1;
    else counts.skip += 1;
  }
  return counts;
}

/** A check worth reading first: it did not pass and has output to show. */
export function opensByDefault(check: Check): boolean {
  const status = check.status.trim().toLowerCase();
  return (status === "fail" || status === "warn") && check.evidence.trim() !== "";
}

/** What an operator says after a re-run, for the live region. */
export function verdictAnnouncement(domain: string, diagnosis: Diagnosis, locale: Locale = getLocale()): string {
  const verdict = verdictView(diagnosis.verdict, locale).word;
  const cause = diagnosis.probable_cause ?? causeFallback(diagnosis.verdict, locale);
  return translate(locale, "appPages.diagnose.announcement", { domain, verdict, cause });
}

/** Which probe explains the verdict best, first: the service before its port, the port before HTTP. */
const CAUSE_ORDER = ["unit", "oom", "port", "http_direct", "http_nginx", "certificate", "disk", "last_deployment", "nginx_log", "journal"] as const;

function statusOf(check: Check): string {
  return check.status.trim().toLowerCase();
}

/**
 * The check the verdict hangs on: the first that failed, in the order a cause is looked for
 * (a stopped service explains a silent port, not the other way round), else the first that
 * warned. Null for a healthy app.
 */
export function leadingCheck(checks: readonly Check[]): Check | null {
  for (const wanted of ["fail", "warn"]) {
    const known = CAUSE_ORDER.map((name) => checks.find((check) => check.name === name && statusOf(check) === wanted)).find(
      (check) => check !== undefined,
    );
    if (known !== undefined) return known;
    const other = checks.find((check) => statusOf(check) === wanted);
    if (other !== undefined) return other;
  }
  return null;
}

/**
 * The verdict in the product's words, above the system's: "The app stops when it starts",
 * never "Result=exit-code" as a headline. Falls back to the sentence for no single cause.
 */
export function headline(diagnosis: Pick<Diagnosis, "verdict" | "checks">, locale: Locale = getLocale()): string {
  const leading = diagnosis.verdict.trim().toLowerCase() === "healthy" ? null : leadingCheck(diagnosis.checks);
  switch (leading?.name) {
    case "unit":
      return translate(locale, "appPages.diagnose.headline.unit");
    case "oom":
      return translate(locale, "appPages.diagnose.headline.oom");
    case "port":
      return translate(locale, "appPages.diagnose.headline.port");
    case "http_direct":
      return translate(locale, "appPages.diagnose.headline.httpDirect");
    case "http_nginx":
      return translate(locale, "appPages.diagnose.headline.httpNginx");
    case "certificate":
      return translate(locale, "appPages.diagnose.headline.certificate");
    case "disk":
      return translate(locale, "appPages.diagnose.headline.disk");
    case "last_deployment":
      return translate(locale, "appPages.diagnose.headline.lastDeployment");
    case "nginx_log":
      return translate(locale, "appPages.diagnose.headline.nginxLog");
    case "journal":
      return translate(locale, "appPages.diagnose.headline.journal");
    default:
      return causeFallback(diagnosis.verdict, locale);
  }
}

/** A check that did not pass: failed, warned, or said something the console does not know. */
export function isProblem(check: Check): boolean {
  const status = statusOf(check);
  return status === "fail" || status === "warn";
}

/**
 * The checks whose output opens with the page: the one the verdict hangs on, and the journal's
 * last lines whenever the app is not healthy, since they are what a cause usually cites. The
 * rest stays folded, one click away.
 */
export function openChecks(diagnosis: Pick<Diagnosis, "verdict" | "checks">): ReadonlySet<string> {
  const open = new Set<string>();
  if (diagnosis.verdict.trim().toLowerCase() === "healthy") return open;
  const leading = leadingCheck(diagnosis.checks);
  if (leading !== null && leading.evidence.trim() !== "") open.add(leading.name);
  else {
    const first = diagnosis.checks.find((check) => opensByDefault(check));
    if (first !== undefined) open.add(first.name);
  }
  const journal = diagnosis.checks.find((check) => check.name === "journal");
  if (journal !== undefined && journal.evidence.trim() !== "") open.add(journal.name);
  return open;
}
