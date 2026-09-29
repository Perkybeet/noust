/**
 * What the Server page reads beyond the raw API shape: the health verdict and each check's
 * status in the console's state language.
 *
 * Labels are message keys, not text: this module has no language, so whoever renders a view
 * (`ServerPage.tsx`) calls `t()`. `verdict.message`, `check.value` and the report's own
 * issues and warnings are `collect_health_report`'s own words (noust.managers.health) and are
 * shown verbatim, never translated.
 */

import type { Status } from "../../components/ui/StatusPill";
import type { SystemHealth } from "../../api/queries/system";
import type { T } from "../../i18n";

export type Verdict = SystemHealth["verdict"];
export type HealthCheck = SystemHealth["checks"][number];

export type VerdictLabel =
  | { key: "healthy" }
  | { key: "needsAttention" }
  | { key: "critical" }
  | { key: "unknown"; raw: string };

export interface VerdictView {
  state: Status;
  label: VerdictLabel;
}

/** `collect_health_report`'s verdict: "error", "warning" or "healthy" (noust.managers.health). */
export function verdictView(verdict: string): VerdictView {
  switch (verdict) {
    case "healthy":
      return { state: "running", label: { key: "healthy" } };
    case "warning":
      return { state: "warning", label: { key: "needsAttention" } };
    case "error":
      return { state: "failed", label: { key: "critical" } };
    default:
      return { state: "unknown", label: { key: "unknown", raw: verdict } };
  }
}

export type CheckLabel = { key: "ok" } | { key: "warning" } | { key: "error" } | { key: "info" };

export interface CheckView {
  state: Status;
  label: CheckLabel;
}

/** One check's status: "ok", "warning", "error" or "info". */
export function checkView(status: string): CheckView {
  switch (status) {
    case "ok":
      return { state: "running", label: { key: "ok" } };
    case "warning":
      return { state: "warning", label: { key: "warning" } };
    case "error":
      return { state: "failed", label: { key: "error" } };
    default:
      return { state: "unknown", label: { key: "info" } };
  }
}

/** Turns a `VerdictLabel` or `CheckLabel` into the word it stands for, in the active language. */
export function verdictText(t: T, label: VerdictLabel | CheckLabel): string {
  switch (label.key) {
    case "healthy":
      return t("server.health.verdictHealthy");
    case "needsAttention":
      return t("server.health.verdictNeedsAttention");
    case "critical":
      return t("server.health.verdictCritical");
    case "unknown":
      return label.raw;
    case "ok":
      return t("server.health.checkOk");
    case "warning":
      return t("server.health.checkWarning");
    case "error":
      return t("server.health.checkError");
    case "info":
      return t("server.health.checkInfo");
  }
}

/**
 * `noust health` names its checks in Title Case for the terminal ("Disk Space"); the console
 * writes labels in sentence case. Known names are reworded, anything else is shown as sent
 * (the backend's word beats a guess, so it stays in English rather than a wrong translation).
 */
export function checkName(t: T, name: string): string {
  switch (name) {
    case "Disk Space":
      return t("server.health.checkDiskSpace");
    case "SSL Certificates":
      return t("server.health.checkSslCertificates");
    default:
      return name;
  }
}

/** A reason for the verdict: an issue fails the check, a warning only needs attention. */
export interface HealthReason {
  level: "issue" | "warning";
  message: string;
  /** The certificate the message is about, when it is about one, to link to it. */
  certificate: CertificateMention | null;
}

export interface CertificateMention {
  /** The message up to the certificate's name: "Certificate for ". */
  before: string;
  name: string;
  /** The rest of the message: " expired 3 days ago". */
  after: string;
}

/**
 * The certificate a health message names. `collect_health_report` words each one as
 * "Certificate for <name> expired N days ago", "... expires in N days" or "... has an
 * unreadable expiry date" (noust.managers.health._check_certificates), with the certbot lineage
 * name, which carries no spaces.
 *
 * The contract is that wording, not a field: src/noust/managers/health.py builds these strings
 * with f"Certificate for {label} ..." where the label comes from _certificate_label (the lineage
 * name, else the first covered domain). Rewording them there, or naming a certificate with
 * something that can hold a space, silently stops the link from appearing here: change this
 * pattern and its cases in data.test.ts in the same commit.
 */
export function certificateMention(message: string): CertificateMention | null {
  const match = /^(Certificate for )(\S+)( .+)$/.exec(message);
  if (match === null) return null;
  const [, before = "", name = "", after = ""] = match;
  return { before, name, after };
}

/** The report's issues, then its warnings: why the verdict is what it is, most serious first. */
export function healthReasons(report: Pick<SystemHealth, "issues" | "warnings">): HealthReason[] {
  return [
    ...report.issues.map((message) => ({ level: "issue" as const, message, certificate: certificateMention(message) })),
    ...report.warnings.map((message) => ({ level: "warning" as const, message, certificate: certificateMention(message) })),
  ];
}
