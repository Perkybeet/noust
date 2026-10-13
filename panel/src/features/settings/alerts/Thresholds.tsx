/**
 * The checks the monitor makes without a rule: how full a disk may get, how many days before a
 * certificate, a missing backup or an API token is announced, and how often each is looked at.
 * Each is one `monitor.*` setting, written through the configuration like the rest of the
 * server's settings; the server's rules name the key they refuse and it lands beside its field.
 */

import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { FormFailure } from "../SettingsForm";
import type { SettingsForm } from "../useSettingsForm";

/** One built-in check's setting: the key under `monitor`, its default, and its words. */
export interface CheckSetting {
  key: string;
  /** What the server uses when the file says nothing (noust.core.config.DEFAULT_CONFIG). */
  fallback: number;
  label: PlainKey;
  description: PlainKey;
  /** Whole numbers only, but for the disk's percentage. */
  decimal?: true;
}

export const CHECK_SETTINGS: readonly CheckSetting[] = [
  { key: "disk_threshold", fallback: 90, decimal: true, label: "alerts.thresholds.diskThreshold.label", description: "alerts.thresholds.diskThreshold.description" },
  { key: "disk_check_seconds", fallback: 0, label: "alerts.thresholds.diskCheck.label", description: "alerts.thresholds.diskCheck.description" },
  { key: "disk_renotify_hours", fallback: 0, label: "alerts.thresholds.diskRenotify.label", description: "alerts.thresholds.diskRenotify.description" },
  { key: "cert_warning_days", fallback: 14, label: "alerts.thresholds.certWarning.label", description: "alerts.thresholds.certWarning.description" },
  { key: "backup_missing_days", fallback: 2, label: "alerts.thresholds.backupMissing.label", description: "alerts.thresholds.backupMissing.description" },
  { key: "backup_check_hours", fallback: 6, label: "alerts.thresholds.backupCheck.label", description: "alerts.thresholds.backupCheck.description" },
  { key: "backup_missing_renotify_hours", fallback: 24, label: "alerts.thresholds.backupRenotify.label", description: "alerts.thresholds.backupRenotify.description" },
  { key: "os_patches_check_hours", fallback: 24, label: "alerts.thresholds.patchesCheck.label", description: "alerts.thresholds.patchesCheck.description" },
  { key: "token_warning_days", fallback: 7, label: "alerts.thresholds.tokenWarning.label", description: "alerts.thresholds.tokenWarning.description" },
];

export const CHECK_KEYS: readonly string[] = CHECK_SETTINGS.map((setting) => setting.key);

/** What the configuration holds for each check, as the strings the form shows. */
export function readChecks(monitor: unknown): Record<string, string> {
  const block = typeof monitor === "object" && monitor !== null ? (monitor as Record<string, unknown>) : {};
  return Object.fromEntries(
    CHECK_SETTINGS.map((setting) => {
      const value = block[setting.key];
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
export const CHECK_CONFIG_KEYS: Readonly<Record<string, string>> = Object.fromEntries(CHECK_KEYS.map((key) => [`monitor.${key}`, key]));

export function Thresholds({ form }: { form: SettingsForm<Record<string, string>> }) {
  const t = useT();
  const values = form.values;
  return (
    <Section title={t("alerts.thresholds.title")} description={t("alerts.thresholds.description")}>
      <FormFailure error={form.formError} title={t("alerts.thresholds.errorTitle")} />
      <Card>
        <div className="grid min-w-0 gap-x-6 gap-y-5 sm:grid-cols-2">
          {CHECK_SETTINGS.map((setting) => (
            <Field key={setting.key} label={t(setting.label)} description={t(setting.description)} error={form.fieldErrors[setting.key]}>
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
      </Card>
    </Section>
  );
}
