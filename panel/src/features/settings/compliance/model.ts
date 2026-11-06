/**
 * How the compliance settings read: a control's name and sentence, its value in words, a
 * framework's one line, and what a warning asks of the person. Everything the server decides (what a
 * framework turns on, what cannot be loosened) arrives in the answer; this only words it.
 */

import type { ComplianceControl, ComplianceFinding, ComplianceFramework } from "../../../api/queries/compliance";
import type { ConsolePath } from "../../../app/navCore";
import { hasMessage } from "../../../i18n";
import type { PlainKey, T } from "../../../i18n";

/** A value the server sent as text; nothing for anything that is not a word or a number. */
export function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return "";
}

/**
 * The controls about getting into the console: Access security draws them as settings, so the list of controls on
 * Compliance leaves them out. In the order that page lists them.
 */
export const ACCESS_PAGE_CONTROLS: readonly string[] = [
  "second_factor",
  "password_min_length",
  "lockout_threshold",
  "lockout_minutes",
  "session_idle_minutes",
  "session_absolute_hours",
  "sudo_password",
  "sudo_idle_minutes",
  "sudo_max_minutes",
  "master_token",
];

/** The groups a screen lists controls in, in the order they read. */
export const GROUPS = ["access", "accountability", "backup", "network"] as const;
export type Group = (typeof GROUPS)[number];

const FRAMEWORK_SUMMARY = {
  "ens-basica": "complianceSetup.frameworks.summary.ensBasica",
  "ens-media": "complianceSetup.frameworks.summary.ensMedia",
  "ens-alta": "complianceSetup.frameworks.summary.ensAlta",
  "iso-27001": "complianceSetup.frameworks.summary.iso27001",
  nis2: "complianceSetup.frameworks.summary.nis2",
  "cis-ig1": "complianceSetup.frameworks.summary.cisIg1",
  "cis-ig2": "complianceSetup.frameworks.summary.cisIg2",
  "soc2-security": "complianceSetup.frameworks.summary.soc2Security",
  "gdpr-32": "complianceSetup.frameworks.summary.gdpr32",
} as const satisfies Record<string, PlainKey>;

/** One line on what a framework is; the server's own English line for a framework this console does not know. */
export function frameworkSummary(t: T, framework: Pick<ComplianceFramework, "id" | "summary">): string {
  return Object.hasOwn(FRAMEWORK_SUMMARY, framework.id)
    ? t(FRAMEWORK_SUMMARY[framework.id as keyof typeof FRAMEWORK_SUMMARY])
    : framework.summary;
}

/** A control's name; its id for a control this console does not know yet. */
export function controlTitle(t: T, id: string): string {
  return hasMessage(`complianceSetup.control.${id}.title`) ? t(`complianceSetup.control.${id}.title` as PlainKey) : id;
}

/** What a control does, in a sentence; empty for a control this console does not know yet. */
export function controlSummary(t: T, id: string): string {
  return hasMessage(`complianceSetup.control.${id}.summary`) ? t(`complianceSetup.control.${id}.summary` as PlainKey) : "";
}

const UNITS = {
  minutes: "complianceSetup.unit.minutes",
  hours: "complianceSetup.unit.hours",
  days: "complianceSetup.unit.days",
  characters: "complianceSetup.unit.characters",
  attempts: "complianceSetup.unit.attempts",
} as const;

/** A control's value in words: "On", "15 minutes", "No limit", "Refused". */
export function formatValue(t: T, control: Pick<ComplianceControl, "kind" | "unit">, value: unknown): string {
  if (control.kind === "flag") return value === true ? t("complianceSetup.value.on") : t("complianceSetup.value.off");
  if (value === null || value === undefined) return t("complianceSetup.value.none");
  if (typeof value === "number") {
    return Object.hasOwn(UNITS, control.unit) ? t(UNITS[control.unit as keyof typeof UNITS], { count: value }) : String(value);
  }
  const word = asText(value);
  return hasMessage(`complianceSetup.choice.${word}`) ? t(`complianceSetup.choice.${word}` as PlainKey) : word;
}

/** The framework names a control is locked by, as a list a sentence can hold. */
export function lockedBy(frameworks: readonly ComplianceFramework[], control: ComplianceControl): string {
  const names = new Map(frameworks.map((framework) => [framework.id, framework.name]));
  return control.forced_by.map((id) => names.get(id) ?? id).join(", ");
}

/** What a warning of a plan says, in words the person can act on. */
export function warningText(t: T, warning: { id: string; count?: number | null | undefined }): string {
  switch (warning.id) {
    case "no_security_officer":
      return t("complianceSetup.warning.no_security_officer");
    case "separation_conflicts":
      return t("complianceSetup.warning.separation_conflicts", { count: warning.count ?? 0 });
    default:
      return warning.id;
  }
}

/** Controls of a group, in the order the server lists them. */
export function inGroup(controls: readonly ComplianceControl[], group: string): ComplianceControl[] {
  return controls.filter((control) => control.group === group);
}

/** How many controls a framework asks for more than the default of: what enabling it changes. */
export function changeCount(framework: ComplianceFramework): number {
  return framework.changes.length;
}

/** Where a control that is not met is mended, as a page of the console and the words of the way there. */
export interface FixLink {
  to: ConsolePath;
  label: PlainKey;
}

const ACCOUNTS: FixLink = { to: "/settings/accounts", label: "complianceSetup.fix.accounts" };
const ACCESS: FixLink = { to: "/settings/access-security", label: "complianceSetup.fix.accessSecurity" };
const BACKUPS: FixLink = { to: "/backups", label: "complianceSetup.fix.backups" };

/**
 * The page of the console that mends each control a check can name, so a finding is never a dead end: the question
 * "what do I do about it" has a link. A control that no page of the console mends has none, and the server's own
 * remediation, which is always shown, is all it says.
 */
export const FIX_LINKS: Readonly<Record<string, FixLink>> = {
  second_factor: ACCOUNTS,
  separation_of_duties: ACCOUNTS,
  single_role_per_account: ACCOUNTS,
  roles_split: ACCOUNTS,
  sso_no_admin: ACCOUNTS,
  access_review_days: ACCOUNTS,
  stale_account_days: ACCOUNTS,
  approvals: { to: "/settings/approvals", label: "complianceSetup.fix.approvals" },
  restricted_tokens: { to: "/settings/tokens", label: "complianceSetup.fix.tokens" },
  token_max_days: { to: "/settings/tokens", label: "complianceSetup.fix.tokens" },
  master_token: ACCESS,
  session_idle_minutes: ACCESS,
  session_absolute_hours: ACCESS,
  lockout_threshold: ACCESS,
  lockout_minutes: ACCESS,
  password_min_length: ACCESS,
  sudo_password: ACCESS,
  sudo_idle_minutes: ACCESS,
  sudo_max_minutes: ACCESS,
  audit_retention_days: { to: "/settings/audit", label: "complianceSetup.fix.audit" },
  audit_review_days: { to: "/settings/audit", label: "complianceSetup.fix.audit" },
  external_audit_sink: { to: "/settings/log-shipping", label: "complianceSetup.fix.logShipping" },
  log_shipping_tls: { to: "/settings/log-shipping", label: "complianceSetup.fix.logShipping" },
  backup_encryption: BACKUPS,
  backup_verify_days: BACKUPS,
  backup_max_age_hours: BACKUPS,
  backup_offsite: BACKUPS,
  console_exposure: { to: "/settings/console", label: "complianceSetup.fix.console" },
};

/** The findings that need somebody (not met first, then needing attention): what is met, and what does not apply, is left out. */
export function findingsToMend(findings: readonly ComplianceFinding[]): ComplianceFinding[] {
  const rank: Readonly<Record<string, number>> = { fail: 0, warning: 1 };
  return findings.filter((finding) => finding.status in rank).sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9));
}
