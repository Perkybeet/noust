import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { calendarWords } from "../../cron/data";
import { WEEKDAYS, scheduleExpression, weekdayLabel } from "../../databases/backups/schedule";
import type { Frequency, ScheduleForm, Weekday } from "../../databases/backups/schedule";

export interface ScheduleFieldsProps {
  value: ScheduleForm;
  onChange: (value: ScheduleForm) => void;
  /** The error under the frequency or the expression (a refusal of the calendar). */
  scheduleError?: string | undefined;
  /** The error under the time of day. */
  timeError?: string | undefined;
}

/**
 * When a policy runs: how often, at what time (the server's clock), or a calendar expression of
 * its own, said back in plain words as it is chosen. The one form every policy that has a timer
 * uses, so "every day at 02:00" is chosen the same way for a database, an application's data and
 * Noust's own state.
 */
export function ScheduleFields({ value, onChange, scheduleError, timeError }: ScheduleFieldsProps) {
  const t = useT();
  const expression = scheduleExpression(value);
  const words = value.frequency === "custom" ? null : calendarWords(value.frequency, expression, t.locale);
  const set = (patch: Partial<ScheduleForm>): void => onChange({ ...value, ...patch });
  return (
    <fieldset className="flex min-w-0 flex-col gap-5">
      <legend className="mb-3 text-13 font-medium text-fg">{t("databases.policy.when")}</legend>
      <Field label={t("databases.policy.frequency")} nativeLabel={false} error={scheduleError}>
        <Select<Frequency>
          value={value.frequency}
          onValueChange={(frequency) => set({ frequency })}
          options={[
            { value: "hourly", label: t("databases.policy.hourly") },
            { value: "daily", label: t("databases.policy.daily") },
            { value: "weekly", label: t("databases.policy.weekly") },
            { value: "monthly", label: t("databases.policy.monthly") },
            { value: "custom", label: t("databases.policy.custom") },
          ]}
        />
      </Field>
      {value.frequency === "weekly" ? (
        <Field label={t("databases.policy.weekday")} nativeLabel={false}>
          <Select<Weekday> value={value.weekday} onValueChange={(weekday) => set({ weekday })} options={WEEKDAYS.map((day) => ({ value: day, label: weekdayLabel(day, t.locale) }))} />
        </Field>
      ) : null}
      {value.frequency === "monthly" ? (
        <Field label={t("databases.policy.dayOfMonth")} nativeLabel={false} description={t("databases.policy.dayOfMonthHelp")}>
          <Select value={value.day} onValueChange={(day) => set({ day })} options={Array.from({ length: 28 }, (_, index) => ({ value: String(index + 1), label: String(index + 1) }))} />
        </Field>
      ) : null}
      {value.frequency === "daily" || value.frequency === "weekly" || value.frequency === "monthly" ? (
        <Field label={t("databases.policy.time")} description={t("databases.policy.timeHelp")} error={timeError}>
          <Input mono value={value.time} onValueChange={(time: string) => set({ time })} placeholder="02:00" className="w-32" autoComplete="off" />
        </Field>
      ) : null}
      {value.frequency === "custom" ? (
        <Field label={t("databases.policy.expression")} description={t("databases.policy.expressionHelp")} error={scheduleError}>
          <Input mono value={value.custom} onValueChange={(custom: string) => set({ custom })} placeholder="Mon..Fri *-*-* 03:30:00" autoComplete="off" spellCheck={false} />
        </Field>
      ) : null}
      <p className="text-13 text-fg-muted" aria-live="polite">
        {words !== null ? t("databases.policy.preview", { words }) : t("databases.policy.previewCustom")} <Mono tone="faint">{expression}</Mono>
      </p>
    </fieldset>
  );
}
