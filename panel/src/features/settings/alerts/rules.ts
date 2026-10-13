/**
 * An alert rule as a form holds it, and as the sentence that says what it watches.
 *
 * The server owns what a rule means (noust.core.alerts.model): the console only turns what an
 * operator types (minutes, "512MB") into what the API takes and back, and never decides whether
 * a limit is acceptable - the server's refusal is shown beside the field it names.
 */

import type { AlertMetric, AlertRule, AlertRuleBody } from "../../../api/queries/alerts";
import type { Locale, PlainKey, T } from "../../../i18n";
import { formatBytes, formatBytesRate, formatDuration, formatPercent } from "../../../lib/format";

export type Comparator = ">" | ">=" | "<" | "<=";
export type RuleSeverity = "warning" | "failed";

export const COMPARATORS: readonly Comparator[] = [">", ">=", "<", "<="];

/** The catalog key of each comparator's word and of each rule's sentence. */
export const COMPARATOR_KEYS: Readonly<Record<Comparator, "gt" | "gte" | "lt" | "lte">> = { ">": "gt", ">=": "gte", "<": "lt", "<=": "lte" };

const METRIC_LABELS: Readonly<Record<string, PlainKey>> = {
  cpu: "alerts.metrics.cpu",
  memory: "alerts.metrics.memory",
  swap: "alerts.metrics.swap",
  disk: "alerts.metrics.disk",
  load: "alerts.metrics.load",
  net_rx: "alerts.metrics.netRx",
  net_tx: "alerts.metrics.netTx",
  app_cpu: "alerts.metrics.appCpu",
  app_memory: "alerts.metrics.appMemory",
  app_requests: "alerts.metrics.appRequests",
  app_5xx: "alerts.metrics.app5xx",
  app_disk_read: "alerts.metrics.appDiskRead",
  app_disk_write: "alerts.metrics.appDiskWrite",
  app_net_rx: "alerts.metrics.appNetRx",
  app_net_tx: "alerts.metrics.appNetTx",
};

/** A measurement in words; one this console does not know keeps the server's own name for it. */
export function metricLabel(t: T, id: string): string {
  const key = METRIC_LABELS[id];
  return key === undefined ? id : t(key);
}

/** The unit's hint under the limit field. */
export function limitHint(t: T, unit: string): string {
  switch (unit) {
    case "percent":
      return t("alerts.form.limitHint.percent");
    case "bytes":
      return t("alerts.form.limitHint.bytes");
    case "bytes_s":
      return t("alerts.form.limitHint.bytes_s");
    case "per_min":
      return t("alerts.form.limitHint.per_min");
    default:
      return t("alerts.form.limitHint.number");
  }
}

/** A reading or a limit as a person reads it: "93%", "512 MB", "2.1 MB/s", "120/min". */
export function formatReading(unit: string, value: number, locale: Locale): string {
  switch (unit) {
    case "percent":
      return formatPercent(value, locale);
    case "bytes":
      return formatBytes(value, locale);
    case "bytes_s":
      return formatBytesRate(value, locale);
    case "per_min":
      return `${String(Math.round(value))}/min`;
    default:
      return String(Math.round(value * 100) / 100);
  }
}

const SIZE_STEPS: readonly (readonly [string, number])[] = [
  ["TB", 1024 ** 4],
  ["GB", 1024 ** 3],
  ["MB", 1024 ** 2],
  ["KB", 1024],
];

/**
 * A limit as it is typed back into the field, in the shortest form the server reads exactly:
 * "90", "512MB", "2MB/s". A size that is not a whole number of some unit stays in bytes.
 */
export function limitText(unit: string, value: number): string {
  if (unit === "bytes" || unit === "bytes_s") {
    const suffix = unit === "bytes_s" ? "/s" : "";
    for (const [name, size] of SIZE_STEPS) {
      if (value >= size && value % size === 0) return `${String(value / size)}${name}${suffix}`;
    }
    return String(value);
  }
  return String(value);
}

export interface RuleForm {
  /** Fixed once the rule exists: a name is what the rule is. */
  name: string;
  metric: string;
  comparator: Comparator;
  /** As typed: a number, or text such as "512MB". */
  limit: string;
  /** The application's domain, or empty for every application. */
  app: string;
  /** Minutes, as typed. */
  minutes: string;
  severity: RuleSeverity;
  /** Minutes, as typed. */
  cooldownMinutes: string;
  remind: boolean;
  /** In the metric's own unit, as typed. */
  hysteresis: string;
  note: string;
}

/** A new rule's starting values: the server's own defaults (five minutes, an hour). */
export function emptyRule(metric: string): RuleForm {
  return { name: "", metric, comparator: ">", limit: "", app: "", minutes: "5", severity: "warning", cooldownMinutes: "60", remind: false, hysteresis: "0", note: "" };
}

/** The form for a rule that exists. */
export function ruleFormFrom(rule: AlertRule, metrics: readonly AlertMetric[]): RuleForm {
  const unit = metrics.find((metric) => metric.id === rule.metric)?.unit ?? rule.unit;
  const comparator = COMPARATORS.find((candidate) => candidate === rule.comparator) ?? ">";
  return {
    name: rule.name,
    metric: rule.metric,
    comparator,
    limit: limitText(unit, rule.threshold),
    app: rule.app_domain ?? "",
    minutes: String(Math.round(rule.duration_s / 60)),
    severity: rule.severity === "failed" ? "failed" : "warning",
    cooldownMinutes: String(Math.round(rule.cooldown_s / 60)),
    remind: rule.remind,
    hysteresis: String(rule.hysteresis),
    note: rule.note,
  };
}

/** A number typed in a field; text that is not one is sent as typed so the server's message lands beside the field. */
function numberOr<T>(text: string, scale: number, fallback: T): number | T {
  const trimmed = text.trim();
  if (!/^\d+(?:\.\d+)?$/.test(trimmed)) return fallback;
  return Math.round(Number(trimmed) * scale);
}

/** The PUT body for a form. */
export function ruleBody(form: RuleForm, enabled: boolean): AlertRuleBody {
  const hysteresis = form.hysteresis.trim() === "" ? 0 : Number(form.hysteresis);
  return {
    metric: form.metric,
    comparator: form.comparator,
    threshold: form.limit.trim(),
    app_domain: form.app.trim() === "" ? null : form.app.trim(),
    duration_s: numberOr(form.minutes, 60, 0),
    hysteresis: Number.isFinite(hysteresis) ? hysteresis : 0,
    cooldown_s: numberOr(form.cooldownMinutes, 60, -1),
    severity: form.severity,
    enabled,
    remind: form.remind,
    note: form.note.trim(),
  };
}

/** "CPU above 90% for 5m": the rule in one line, from the pieces the catalog words whole. */
export function ruleSentence(t: T, rule: AlertRule, unit: string): string {
  const comparator = COMPARATORS.find((candidate) => candidate === rule.comparator) ?? ">";
  return t(`alerts.rules.sentence.${COMPARATOR_KEYS[comparator]}`, {
    metric: metricLabel(t, rule.metric),
    limit: formatReading(unit, rule.threshold, t.locale),
    duration: formatDuration(rule.duration_s, t.locale),
  });
}

/** Where a rule applies, in a sentence. */
export function appliesTo(t: T, rule: AlertRule, scope: string): string {
  if (scope !== "app") return t("alerts.rules.appliesTo.server");
  return rule.app_domain === null || rule.app_domain === undefined ? t("alerts.rules.appliesTo.every") : t("alerts.rules.appliesTo.app", { app: rule.app_domain });
}

/** The fields of the form, by the name the API's refusals use for them. */
export const RULE_FIELDS = ["metric", "comparator", "threshold", "app_domain", "duration", "duration_s", "hysteresis", "cooldown", "cooldown_s", "severity", "name", "note"] as const;
export type RuleField = (typeof RULE_FIELDS)[number];
