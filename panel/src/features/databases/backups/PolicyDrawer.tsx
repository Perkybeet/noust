import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { backupDestinationsQuery } from "../../../api/queries/backupDestinations";
import { databaseKeys } from "../../../api/queries/databases";
import type { BackupPolicy, PolicyBody } from "../../../api/queries/databases";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { calendarWords } from "../../cron/data";
import { WEEKDAYS, scheduleExpression, scheduleForm, validTime, weekdayLabel } from "./schedule";
import type { Frequency, ScheduleForm, Weekday } from "./schedule";

interface DestinationDraft {
  name: string;
  count: string;
  days: string;
}

/** A retention limit as the form holds it: blank for "no limit", which the API takes as null. */
function limitText(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

function limitValue(text: string): number | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : Number(trimmed);
}

function validLimit(text: string, max: number): boolean {
  const trimmed = text.trim();
  if (trimmed === "") return true;
  const value = Number(trimmed);
  return Number.isInteger(value) && value >= 1 && value <= max;
}

export interface PolicyDrawerProps {
  engine: string;
  name: string;
  policy: BackupPolicy | undefined;
  /** PostgreSQL chooses its dump format. */
  formats: boolean;
  /** Test-restoring each dump is not available for Redis. */
  testRestore: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * A database's backup policy, as one form in a drawer: when (said back in plain words), how
 * many dumps and for how long to keep them here, where else each one goes (with that place's
 * own retention), and whether each dump is proven by loading it into a throwaway database.
 * Saving it installs its timer, in sudo mode.
 */
export function PolicyDrawer({ engine, name, policy, formats, testRestore, open, onOpenChange }: PolicyDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: open });
  const configured = policy?.configured === true;
  const [schedule, setSchedule] = useState<ScheduleForm>(() => scheduleForm(configured ? policy.schedule : null));
  const [count, setCount] = useState(configured ? limitText(policy.retention_count) : "7");
  const [days, setDays] = useState(configured ? limitText(policy.retention_days) : "30");
  const [sends, setSends] = useState<DestinationDraft[]>(() =>
    configured ? (policy.destinations ?? []).map((item) => ({ name: item.name, count: limitText(item.retention_count), days: limitText(item.retention_days) })) : [],
  );
  const [format, setFormat] = useState(policy?.dump_format ?? "custom");
  const [verify, setVerify] = useState(policy?.verify_restore ?? false);
  const [enabled, setEnabled] = useState(configured ? policy.enabled : true);
  const [errors, setErrors] = useState<Partial<Record<"schedule" | "time" | "count" | "days" | "destinations", string>>>({});

  const save = useMutation({
    mutationFn: () => {
      const body: PolicyBody = {
        schedule: scheduleExpression(schedule),
        retention_count: limitValue(count),
        retention_days: limitValue(days),
        destinations: sends.map((item) => ({ name: item.name, retention_count: limitValue(item.count), retention_days: limitValue(item.days) })),
        dump_format: formats ? format : null,
        verify_restore: testRestore && verify,
        enabled,
      };
      return request("put", "/api/databases/backup-policies/{engine}/{database}", { params: { engine, database: name }, body });
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(databaseKeys.policy(engine, name), saved);
      void queryClient.invalidateQueries({ queryKey: databaseKeys.policies });
      onOpenChange(false);
    },
    onError: (error) => {
      if (isApiError(error) && error.fields?.["schedule"]) setErrors({ schedule: error.fields["schedule"] });
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (schedule.frequency === "custom" && schedule.custom.trim() === "") found.schedule = t("databases.policy.customRequired");
    if (schedule.frequency !== "hourly" && schedule.frequency !== "custom" && !validTime(schedule.time)) found.time = t("databases.policy.timeInvalid");
    if (!validLimit(count, 365)) found.count = t("databases.policy.countInvalid");
    if (!validLimit(days, 3650)) found.days = t("databases.policy.daysInvalid");
    if (sends.some((item) => !validLimit(item.count, 365) || !validLimit(item.days, 3650))) found.destinations = t("databases.policy.destinationLimitInvalid");
    setErrors(found);
    if (Object.keys(found).length === 0) save.mutate();
  };

  const expression = scheduleExpression(schedule);
  const words = schedule.frequency === "custom" ? null : calendarWords(schedule.frequency, expression, t.locale);
  const available = destinations.data?.destinations ?? [];
  const set = (patch: Partial<ScheduleForm>): void => setSchedule((previous) => ({ ...previous, ...patch }));
  const formId = "backup-policy-form";

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={configured ? t("databases.policy.editTitle") : t("databases.policy.newTitle")}
      description={t("databases.policy.description", { name })}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("databases.policy.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-6">
        <fieldset className="flex min-w-0 flex-col gap-5">
          <legend className="mb-3 text-13 font-medium text-fg">{t("databases.policy.when")}</legend>
          <Field label={t("databases.policy.frequency")} nativeLabel={false} error={errors.schedule}>
            <Select<Frequency>
              value={schedule.frequency}
              onValueChange={(value) => set({ frequency: value })}
              options={[
                { value: "hourly", label: t("databases.policy.hourly") },
                { value: "daily", label: t("databases.policy.daily") },
                { value: "weekly", label: t("databases.policy.weekly") },
                { value: "monthly", label: t("databases.policy.monthly") },
                { value: "custom", label: t("databases.policy.custom") },
              ]}
            />
          </Field>
          {schedule.frequency === "weekly" ? (
            <Field label={t("databases.policy.weekday")} nativeLabel={false}>
              <Select<Weekday> value={schedule.weekday} onValueChange={(value) => set({ weekday: value })} options={WEEKDAYS.map((day) => ({ value: day, label: weekdayLabel(day, t.locale) }))} />
            </Field>
          ) : null}
          {schedule.frequency === "monthly" ? (
            <Field label={t("databases.policy.dayOfMonth")} nativeLabel={false} description={t("databases.policy.dayOfMonthHelp")}>
              <Select value={schedule.day} onValueChange={(value) => set({ day: value })} options={Array.from({ length: 28 }, (_, index) => ({ value: String(index + 1), label: String(index + 1) }))} />
            </Field>
          ) : null}
          {schedule.frequency === "daily" || schedule.frequency === "weekly" || schedule.frequency === "monthly" ? (
            <Field label={t("databases.policy.time")} description={t("databases.policy.timeHelp")} error={errors.time}>
              <Input mono value={schedule.time} onValueChange={(value: string) => set({ time: value })} placeholder="02:00" className="w-32" autoComplete="off" />
            </Field>
          ) : null}
          {schedule.frequency === "custom" ? (
            <Field label={t("databases.policy.expression")} description={t("databases.policy.expressionHelp")} error={errors.schedule}>
              <Input mono value={schedule.custom} onValueChange={(value: string) => set({ custom: value })} placeholder="Mon..Fri *-*-* 03:30:00" autoComplete="off" spellCheck={false} />
            </Field>
          ) : null}
          <p className="text-13 text-fg-muted" aria-live="polite">
            {words !== null ? t("databases.policy.preview", { words }) : t("databases.policy.previewCustom")} <Mono tone="faint">{expression}</Mono>
          </p>
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-5">
          <legend className="mb-3 text-13 font-medium text-fg">{t("databases.policy.keepHere")}</legend>
          <div className="flex flex-wrap gap-4">
            <Field label={t("databases.policy.count")} optional description={t("databases.policy.countHelp")} error={errors.count} className="w-44">
              <Input mono inputMode="numeric" value={count} onValueChange={(value: string) => setCount(value)} placeholder="7" />
            </Field>
            <Field label={t("databases.policy.days")} optional description={t("databases.policy.daysHelp")} error={errors.days} className="w-44">
              <Input mono inputMode="numeric" value={days} onValueChange={(value: string) => setDays(value)} placeholder="30" />
            </Field>
          </div>
          <p className="text-12 text-fg-faint">{t("databases.policy.retentionNote")}</p>
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-3">
          <legend className="mb-3 text-13 font-medium text-fg">{t("databases.policy.sendTo")}</legend>
          {destinations.isError ? (
            <ErrorBlock compact error={destinations.error} title={t("databases.policy.destinationsFailed")} onRetry={() => void destinations.refetch()} />
          ) : available.length === 0 && destinations.data !== undefined ? (
            <EmptyState variant="inline" title={t("databases.policy.noDestinations")} description={t("databases.policy.noDestinationsHint")} />
          ) : (
            <ul className="flex flex-col gap-3">
              {available.map((destination) => {
                const chosen = sends.find((item) => item.name === destination.name);
                return (
                  <li key={destination.name} className="flex flex-col gap-2">
                    <Checkbox
                      checked={chosen !== undefined}
                      onCheckedChange={(checked) =>
                        setSends((previous) => (checked ? [...previous, { name: destination.name, count: "", days: "" }] : previous.filter((item) => item.name !== destination.name)))
                      }
                      label={<Mono tone="default">{destination.name}</Mono>}
                    />
                    {chosen !== undefined ? (
                      <div className="flex flex-wrap gap-3 pl-6">
                        <Input
                          size="sm"
                          mono
                          inputMode="numeric"
                          aria-label={t("databases.policy.destinationCount", { destination: destination.name })}
                          placeholder={t("databases.policy.keepAll")}
                          value={chosen.count}
                          onValueChange={(value: string) => setSends((previous) => previous.map((item) => (item.name === destination.name ? { ...item, count: value } : item)))}
                          suffix={t("databases.policy.dumpsSuffix")}
                          className="w-40"
                        />
                        <Input
                          size="sm"
                          mono
                          inputMode="numeric"
                          aria-label={t("databases.policy.destinationDays", { destination: destination.name })}
                          placeholder={t("databases.policy.keepAll")}
                          value={chosen.days}
                          onValueChange={(value: string) => setSends((previous) => previous.map((item) => (item.name === destination.name ? { ...item, days: value } : item)))}
                          suffix={t("databases.policy.daysSuffix")}
                          className="w-40"
                        />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
          {errors.destinations !== undefined ? <p className="text-12 text-fail">{errors.destinations}</p> : null}
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-4">
          <legend className="mb-3 text-13 font-medium text-fg">{t("databases.policy.proof")}</legend>
          {formats ? (
            <Field label={t("databases.policy.format")} nativeLabel={false} description={t("databases.policy.formatHelp")}>
              <Select
                value={format}
                onValueChange={(value) => setFormat(value)}
                options={[
                  { value: "custom", label: t("databases.policy.formatCustom") },
                  { value: "plain", label: t("databases.policy.formatPlain") },
                  { value: "tar", label: t("databases.policy.formatTar") },
                ]}
              />
            </Field>
          ) : null}
          {testRestore ? <Checkbox checked={verify} onCheckedChange={setVerify} label={t("databases.policy.verifyRestore")} description={t("databases.policy.verifyRestoreHelp")} /> : null}
          <Checkbox checked={enabled} onCheckedChange={setEnabled} label={t("databases.policy.enabled")} description={t("databases.policy.enabledHelp")} />
        </fieldset>
        {save.isError && !(isApiError(save.error) && save.error.fields?.["schedule"]) ? <ErrorBlock live compact error={save.error} title={t("databases.policy.saveFailed")} /> : null}
      </form>
    </Drawer>
  );
}
