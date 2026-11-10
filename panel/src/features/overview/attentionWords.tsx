/**
 * What an item that needs attention says, in the console's words: one sentence for each reason the
 * server (or the console) gives, a certificate's expiry and the glyph of its severity. The Home
 * board, the inbox and the fleet read these, so a reason is told one way wherever it is shown.
 */

import type { OverviewAttentionReason } from "../../api/queries/overview";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph } from "../../components/ui/StatusPill";
import type { T } from "../../i18n";
import { formatDate, parseTimestamp } from "../../lib/format";
import { leadingSentence } from "../../lib/serverSentence";
import { namedActor } from "../activity/data";
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
