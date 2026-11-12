/**
 * What an item that needs attention says, in the console's words: one sentence for each reason the
 * server (or the console) gives, a certificate's expiry and the glyph of its severity. The Home
 * board, the inbox and the fleet read these, so a reason is told one way wherever it is shown.
 */

import type { OverviewAttentionItem, OverviewAttentionReason } from "../../api/queries/overview";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph } from "../../components/ui/StatusPill";
import type { T } from "../../i18n";
import { formatBytes, formatDate, formatPercent, parseTimestamp } from "../../lib/format";
import { leadingSentence } from "../../lib/serverSentence";
import { namedActor } from "../activity/data";
import { checkTitle } from "../server/security/data";
import type { Severity } from "./attention";

/**
 * What the monitor saw, in words, by its signal (`src/noust/monitor/models.py`) and severity
 * (a warning asks more firmly than a notice); a signal this console does not know, by name.
 */
function monitorText(t: T, severity: string, signal: string): string {
  const warning = severity === "warning";
  switch (signal) {
    case "name-pattern":
      return warning ? t("overview.attention.monitor.nameWarning") : t("overview.attention.monitor.nameNotice");
    case "resource-usage":
      return warning ? t("overview.attention.monitor.usageWarning") : t("overview.attention.monitor.usageNotice");
    default:
      return t("overview.attention.monitor.other", { signal });
  }
}

/** A certificate's expiry, translated and in the console's date format. */
export function validUntilText(t: T, value: string): string {
  const date = parseTimestamp(value);
  return t("overview.attention.certValidUntil", { date: date ? formatDate(date, {}, t.locale) : value });
}

export function SeverityGlyph({ severity }: { severity: Severity }) {
  return <StatusGlyph state={severity === "fail" ? "failed" : "warning"} size={14} className={severity === "fail" ? "text-fail" : "text-warn"} />;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function numberOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function listOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

/** A calendar date the server gave as `YYYY-MM-DD`, in the console's format; the text itself when it is not one. */
function dayOf(value: string, t: T): string {
  const date = parseTimestamp(value);
  return date ? formatDate(date, {}, t.locale) : value;
}

/** Where a row of the one list comes from, by the name of the area that deals with it; a server before 4.3 sends none, and it is a state. */
export function originLabel(t: T, origin: string | null | undefined): string {
  switch (origin) {
    case "alert":
      return t("inbox.origin.alert");
    case "security":
      return t("inbox.origin.security");
    default:
      return t("inbox.origin.state");
  }
}

/** What a row is called, and how it is drawn: a domain, a unit or a rule is the system's name (never translated), a fact of the machine is what it is about. */
export interface ItemTitle {
  text: string;
  /** In mono: a unit, an alert's rule, a probe, a recipe's application. */
  mono: boolean;
  /** The text is Noust's own words and may be translated by a browser; a name is not. */
  translate: boolean;
}

const MACHINE_TITLES = {
  security_updates: "inbox.machine.securityUpdates",
  packages_broken: "inbox.machine.packagesBroken",
  os_support: "inbox.machine.osSupport",
  disk: "inbox.machine.disk",
  reboot_required: "inbox.machine.rebootRequired",
  clock: "inbox.machine.clock",
  swap: "inbox.machine.swap",
  failed_units: "inbox.machine.failedUnits",
} as const;

export function itemTitle(t: T, item: Pick<OverviewAttentionItem, "subject" | "title">): ItemTitle {
  const kind = item.subject["kind"];
  if (kind === "machine") {
    const fact = text(item.subject["fact"]);
    const key = fact !== null && fact in MACHINE_TITLES ? MACHINE_TITLES[fact as keyof typeof MACHINE_TITLES] : null;
    return { text: key === null ? item.title : t(key), mono: false, translate: true };
  }
  if (kind === "finding") return { text: checkTitle(t, { id: text(item.subject["check"]) ?? "", title: item.title }), mono: false, translate: true };
  return { text: item.title, mono: kind === "alert" || kind === "probe" || kind === "recipe" || kind === "unit", translate: false };
}
/** What the server says is wrong, as a sentence of the console's; a code it does not know, verbatim. */
export function reasonText(t: T, reason: OverviewAttentionReason): string {
  const params = reason.params ?? {};
  switch (reason.code) {
    case "service_failed":
      return t("overview.attention.serviceFailed");
    case "service_restarting":
      return t("overview.attention.serviceRestarting");
    case "running_outside_unit":
      return t("overview.attention.runningOutsideUnit");
    case "no_answer":
      return t("overview.attention.noAnswer");
    case "deploy_failed":
      return t("overview.attention.deployFailed");
    case "first_deploy_failed":
      return t("overview.attention.firstDeployFailed");
    case "deploy_rolled_back":
      return t("overview.attention.deployRolledBack");
    case "certificate_expired":
      return t("overview.attention.certExpired");
    case "certificate_expires_today":
      return t("overview.attention.certExpiresToday");
    case "certificate_expires_in":
      return t("overview.attention.certExpiresIn", { count: Number(params["days"] ?? 0) });
    case "unit_failed":
      return t("overview.attention.unitFailed");
    case "unit_restarting":
      return t("overview.attention.unitRestarting");
    case "monitor_finding":
      return monitorText(t, text(params["severity"]) ?? "", text(params["signal"]) ?? "");
    case "alert_firing":
      return t("inbox.reasons.alertFiring");
    case "alert_taken":
      return t("inbox.reasons.alertTaken", { by: namedActor(t, text(params["by"]) ?? "") });
    // What the console adds to the server's list (features/overview/attention.ts).
    case "probe_down":
      return t("inbox.reasons.probeDown");
    case "approval_waiting":
      return t("inbox.reasons.approvalWaiting", { by: text(params["by"]) ?? "" });
    case "noust_update":
      return t("inbox.reasons.noustUpdate", { version: text(params["version"]) ?? "", current: text(params["current"]) ?? "" });
    case "noust_security_update":
      return t("inbox.reasons.noustSecurityUpdate", { version: text(params["version"]) ?? "", current: text(params["current"]) ?? "" });
    case "recipe_update":
      return t("inbox.reasons.recipeUpdate", { recipe: text(params["recipe"]) ?? "", version: text(params["version"]) ?? "", current: text(params["current"]) ?? "" });
    // What is wrong with the machine (managers/attention.py `machine_items`).
    case "security_updates":
      return t("inbox.reasons.securityUpdates", { count: numberOf(params["count"]) ?? 0 });
    case "packages_broken":
      return t("inbox.reasons.packagesBroken");
    case "reboot_required": {
      const packages = listOf(params["packages"]);
      return packages.length > 0 ? t("inbox.reasons.rebootRequiredBecause", { packages: packages.slice(0, 3).join(", ") }) : t("inbox.reasons.rebootRequired");
    }
    case "os_unsupported": {
      const date = text(params["date"]);
      const name = text(params["name"]) ?? "";
      return date !== null ? t("inbox.reasons.osUnsupportedSince", { name, date: dayOf(date, t) }) : t("inbox.reasons.osUnsupported", { name });
    }
    case "os_ending":
      return t("inbox.reasons.osEnding", { name: text(params["name"]) ?? "", count: numberOf(params["days"]) ?? 0 });
    case "disk_full": {
      const mount = text(params["mount"]) ?? "";
      const percent = formatPercent(numberOf(params["percent"]) ?? 0, t.locale);
      const free = numberOf(params["free"]);
      return free !== null ? t("inbox.reasons.diskFullFree", { mount, percent, free: formatBytes(free, t.locale) }) : t("inbox.reasons.diskFull", { mount, percent });
    }
    case "clock_unsynced":
      return t("inbox.reasons.clockUnsynced");
    case "no_swap":
      return t("inbox.reasons.noSwap");
    case "failed_units": {
      const units = listOf(params["units"]);
      return t("inbox.reasons.failedUnits", { count: numberOf(params["count"]) ?? units.length, units: units.slice(0, 3).join(", ") });
    }
    // A check of the hardening: what it found stays in Noust's own words under this line, as the detail.
    case "security_finding":
      return text(params["severity"]) === "critical" ? t("inbox.reasons.securityFindingCritical") : t("inbox.reasons.securityFinding");
    default:
      return reason.code;
  }
}

/** The line under a reason: `sentence` when it is Noust's own, said in this console's language, else what the system printed. */
export interface ReasonDetail {
  text: string;
  sentence: boolean;
}

/**
 * What a reason says under its sentence. The server marks a detail that is a sentence of Noust's (a release
 * that did not pass its startup check) with its code, and this console says it in its language; any other
 * detail is another program's words and is shown as they came.
 */
export function reasonDetail(t: T, reason: OverviewAttentionReason): ReasonDetail | null {
  const detail = text(reason.detail);
  if (detail === null) return null;
  const coded = reason.detail_coded ?? null;
  const said = leadingSentence(t.locale, coded, detail);
  return { text: said, sentence: coded !== null && said !== detail };
}

/** A reason's detail line: a sentence of Noust's in the text's own face, the system's words in mono, wrapped and never cut. */
export function DetailLine({ detail }: { detail: ReasonDetail }) {
  return detail.sentence ? (
    <span className="line-clamp-2 text-12 text-pretty break-words text-fg-muted">{detail.text}</span>
  ) : (
    <Mono tone="muted" className="line-clamp-2 text-12 break-words whitespace-pre-wrap">
      {detail.text}
    </Mono>
  );
}
