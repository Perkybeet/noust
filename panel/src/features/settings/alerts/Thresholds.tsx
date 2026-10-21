/**
 * The checks the monitor makes without a rule: how full a disk may get, how many days before a
 * certificate, a missing backup or an API token is announced, and how often each is looked at.
 * Each field is one `monitor.*` setting, written through the configuration like the rest of the
 * server's settings; the server's rules name the key they refuse and it lands beside its field.
 *
 * A check that can be off says so with a box of its own: the setting underneath is a number that
 * means "off" at 0, but 0 is not what the operator reads or types - the box is, and its numbers
 * appear when it is ticked. Ticking it again brings back what it held before.
 */

import { useState } from "react";
import { Gauge } from "lucide-react";

import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { FormFailure } from "../SettingsForm";
import type { SettingsForm } from "../useSettingsForm";

/** One number of a check: its key, where it lives, its default, and its words. */
export interface CheckSetting {
  /** The form's name for it; the key under `monitor` unless `path` says where else. */
  key: string;
  /** The dotted configuration key, when it is not under `monitor`. */
  path?: string;
  /** What the server uses when the file says nothing (noust.core.config.DEFAULT_CONFIG). */
  fallback: number;
  label: PlainKey;
  description?: PlainKey;
  /** Whole numbers only, but for the disk's percentage. */
  decimal?: true;
}

/** One check of the monitor: what it announces, whether it can be off, and the numbers it takes. */
export interface Check {
  id: string;
  title: PlainKey;
  description: PlainKey;
  /** The key of the number that is 0 when the check is off; absent when the check cannot be off. */
  off?: string;
  fields: readonly CheckSetting[];
}

export const CHECKS: readonly Check[] = [
  {
    id: "disks",
    title: "alerts.thresholds.disks.title",
    description: "alerts.thresholds.disks.description",
    fields: [
      { key: "disk_threshold", fallback: 90, decimal: true, label: "alerts.thresholds.disks.threshold.label", description: "alerts.thresholds.disks.threshold.description" },
      { key: "disk_check_seconds", fallback: 0, label: "alerts.thresholds.disks.check.label", description: "alerts.thresholds.disks.check.description" },
      { key: "disk_renotify_hours", fallback: 0, label: "alerts.thresholds.disks.renotify.label", description: "alerts.thresholds.disks.renotify.description" },
    ],
  },
  {
    id: "certificates",
    title: "alerts.thresholds.certificates.title",
    description: "alerts.thresholds.certificates.description",
    fields: [{ key: "cert_warning_days", fallback: 14, label: "alerts.thresholds.certificates.warning.label" }],
  },
  {
    id: "backups",
    title: "alerts.thresholds.backups.title",
    description: "alerts.thresholds.backups.description",
    off: "backup_missing_days",
    fields: [
      { key: "backup_missing_days", fallback: 2, label: "alerts.thresholds.backups.missing.label" },
      { key: "backup_check_hours", fallback: 6, label: "alerts.thresholds.backups.check.label" },
      { key: "backup_missing_renotify_hours", fallback: 24, label: "alerts.thresholds.backups.renotify.label" },
    ],
  },
  {
    id: "updates",
    title: "alerts.thresholds.updates.title",
    description: "alerts.thresholds.updates.description",
    off: "os_patches_check_hours",
    fields: [{ key: "os_patches_check_hours", fallback: 24, label: "alerts.thresholds.updates.check.label" }],
  },
  {
    id: "tokens",
    title: "alerts.thresholds.tokens.title",
    description: "alerts.thresholds.tokens.description",
    off: "token_warning_days",
    fields: [{ key: "token_warning_days", fallback: 7, label: "alerts.thresholds.tokens.warning.label" }],
  },
  {
    id: "webserver",
    title: "alerts.thresholds.webserver.title",
    description: "alerts.thresholds.webserver.description",
    off: "webserver_check_seconds",
    fields: [
      { key: "webserver_check_seconds", fallback: 60, label: "alerts.thresholds.webserver.check.label" },
      { key: "webserver_restart_limit", fallback: 3, label: "alerts.thresholds.webserver.restarts.label", description: "alerts.thresholds.webserver.restarts.description" },
    ],
  },
  {
    id: "containers",
    title: "alerts.thresholds.containers.title",
    description: "alerts.thresholds.containers.description",
    off: "container_check_seconds",
    fields: [
      { key: "container_check_seconds", fallback: 60, label: "alerts.thresholds.containers.check.label" },
      { key: "container_restart_limit", fallback: 5, label: "alerts.thresholds.containers.restarts.label", description: "alerts.thresholds.containers.restarts.description" },
    ],
  },
  {
    id: "security",
    title: "alerts.thresholds.security.title",
    description: "alerts.thresholds.security.description",
    off: "security_check_hours",
    fields: [{ key: "security_check_hours", fallback: 6, label: "alerts.thresholds.security.check.label" }],
  },
  {
    id: "recipes",
    title: "alerts.thresholds.recipes.title",
    description: "alerts.thresholds.recipes.description",
    off: "recipe_check_hours",
    fields: [{ key: "recipe_check_hours", fallback: 24, label: "alerts.thresholds.recipes.check.label" }],
  },
  {
    id: "history",
    title: "alerts.thresholds.history.title",
    description: "alerts.thresholds.history.description",
    fields: [
      { key: "metrics_retention_days", path: "metrics.retention_days", fallback: 400, label: "alerts.thresholds.history.retention.label", description: "alerts.thresholds.history.retention.description" },
    ],
  },
];

/** Every number of every check, in the order the page draws them. */
export const CHECK_SETTINGS: readonly CheckSetting[] = CHECKS.flatMap((check) => check.fields);

/** The dotted configuration key a check is read from and written to. */
export function checkPath(setting: CheckSetting): string {
  return setting.path ?? `monitor.${setting.key}`;
}

export const CHECK_KEYS: readonly string[] = CHECK_SETTINGS.map((setting) => setting.key);

/** What the configuration holds for each check, as the strings the form shows. */
export function readChecks(config: unknown): Record<string, string> {
  return Object.fromEntries(
    CHECK_SETTINGS.map((setting) => {
      let value: unknown = config;
      for (const part of checkPath(setting).split(".")) {
        value = typeof value === "object" && value !== null ? (value as Record<string, unknown>)[part] : undefined;
      }
      return [setting.key, typeof value === "number" ? String(value) : String(setting.fallback)];
    }),
  );
}

/** A number as typed, or the text itself so the server's refusal names it; never a guess. */
export function checkValue(text: string): number | string {
  const trimmed = text.trim();
  return /^\d+(?:\.\d+)?$/.test(trimmed) ? Number(trimmed) : trimmed;
}

/** The dotted configuration key of each check, to the field it is about. */
export const CHECK_CONFIG_KEYS: Readonly<Record<string, string>> = Object.fromEntries(CHECK_SETTINGS.map((setting) => [checkPath(setting), setting.key]));

/**
 * Whether a check is on: it has no way to be off, or the number that means "off" is not 0. A field
 * that is being retyped is empty for a moment, and that is not the check being off.
 */
export function isOn(check: Check, values: Record<string, string> | undefined): boolean {
  if (check.off === undefined || values === undefined) return true;
  const held = (values[check.off] ?? "").trim();
  return held === "" || Number(held) !== 0;
}

function CheckRow({ check, form, kept, onKeep }: { check: Check; form: SettingsForm<Record<string, string>>; kept: Readonly<Record<string, string>>; onKeep: (key: string, value: string) => void }) {
  const t = useT();
  const values = form.values;
  const on = isOn(check, values);
  const switchable = check.off !== undefined;
  const offKey = check.off;
  const defaultOn = check.fields.find((field) => field.key === offKey)?.fallback ?? 0;
  const fields = switchable && !on ? [] : check.fields;
  return (
    <div className="flex min-w-0 flex-col gap-3 px-5 py-4">
      {switchable && offKey !== undefined ? (
        <Checkbox
          label={t(check.title)}
          description={t(check.description)}
          checked={on}
          disabled={values === undefined}
          onCheckedChange={(next) => {
            if (next) {
              // Back to what it held before it was ticked off, or to the server's own default.
              form.set(offKey, kept[offKey] ?? String(defaultOn));
            } else {
              onKeep(offKey, values?.[offKey] ?? String(defaultOn));
              form.set(offKey, "0");
            }
          }}
        />
      ) : (
        <div className="flex min-w-0 flex-col">
          <p className="text-13 font-medium text-fg">{t(check.title)}</p>
          <p className="text-12 text-pretty text-fg-muted">{t(check.description)}</p>
        </div>
      )}
      {fields.length > 0 ? (
        <div className={switchable ? "grid min-w-0 gap-x-6 gap-y-4 sm:grid-cols-2 sm:pl-6" : "grid min-w-0 gap-x-6 gap-y-4 sm:grid-cols-2"}>
          {fields.map((setting) => (
            <Field key={setting.key} label={t(setting.label)} {...(setting.description !== undefined ? { description: t(setting.description) } : {})} error={form.fieldErrors[setting.key]}>
              <Input
                mono
                inputMode={setting.decimal === true ? "decimal" : "numeric"}
                value={values?.[setting.key] ?? ""}
                disabled={values === undefined}
                onValueChange={(next: string) => {
                  form.set(setting.key, next);
                }}
              />
            </Field>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function Thresholds({ form }: { form: SettingsForm<Record<string, string>> }) {
  const t = useT();
  // What a check held when it was ticked off, so ticking it again does not forget the number.
  const [kept, setKept] = useState<Readonly<Record<string, string>>>({});
  return (
    <Section icon={Gauge} title={t("alerts.thresholds.title")} description={t("alerts.thresholds.description")}>
      <FormFailure error={form.formError} title={t("alerts.thresholds.errorTitle")} />
      <Card padding="none">
        <div className="flex flex-col divide-y divide-border">
          {CHECKS.map((check) => (
            <CheckRow
              key={check.id}
              check={check}
              form={form}
              kept={kept}
              onKeep={(key, value) => {
                if (value.trim() !== "" && Number(value) !== 0) setKept((current) => ({ ...current, [key]: value }));
              }}
            />
          ))}
        </div>
      </Card>
    </Section>
  );
}
