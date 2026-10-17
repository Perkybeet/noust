/**
 * How the compliance settings read: a control's name and sentence, its value in words, a
 * framework's one line, and what a warning asks of the person. Everything the server decides (what a
 * framework turns on, what cannot be loosened) arrives in the answer; this only words it.
 */

import type { ComplianceControl, ComplianceFramework } from "../../../api/queries/compliance";
import { hasMessage } from "../../../i18n";
import type { PlainKey, T } from "../../../i18n";

/** A value the server sent as text; nothing for anything that is not a word or a number. */
export function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return "";
}

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
