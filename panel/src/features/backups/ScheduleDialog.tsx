import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { appsQuery } from "../../api/queries/apps";
import type { BackupSchedule } from "../../api/queries/backups";
import { backupSchedulesQuery } from "../../api/queries/backups";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { DEFAULT_SCHEDULE, scheduleExpression, scheduleForm, validTime } from "../databases/backups/schedule";
import type { ScheduleForm } from "../databases/backups/schedule";
import { GbField } from "./policy/GbField";
import { KeepFields } from "./policy/KeepFields";
import { MAX_COUNT, MAX_DAYS, destinationsValid, gbValue, limitText, validGb, validLimit } from "./policy/limits";
import type { DestinationDraft } from "./policy/limits";
import { RemoteOnlyField } from "./policy/RemoteOnlyField";
import { ScheduleFields } from "./policy/ScheduleFields";
import { SendToFields } from "./policy/SendToFields";
import type { CreateScheduleInput, ScheduleDestinationInput } from "./useBackupActions";
import { useBackupActions } from "./useBackupActions";

/** The application choice that schedules every application without a schedule at once. */
export const EVERY_APP = "*";

/** What a new schedule keeps unless the operator changes it; the API's own defaults too. */
const NEW_SCHEDULE_KEEP = 7;
const NEW_SCHEDULE_MAX_AGE = 30;

type RetentionMode = "server" | "own";

type ErrorKey = "domain" | "schedule" | "time" | "count" | "days" | "gb" | "destinations";

/** A number typed for an optional field: blank stays unset rather than becoming 0. */
function optionalNumber(value: string): number | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  const parsed = Number(trimmed.replace(",", "."));
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** The destinations as the API's input: a blank limit is none, not zero. */
function destinationInput(sends: readonly DestinationDraft[]): ScheduleDestinationInput[] {
  return sends.map((entry) => ({
    name: entry.name,
    retentionCount: optionalNumber(entry.count),
    retentionDays: optionalNumber(entry.days),
    retentionGb: optionalNumber(entry.gb ?? ""),
  }));
}

export interface ScheduleDialogProps {
  /** Present to edit a schedule; absent to create one. */
  existing?: BackupSchedule;
  /** Preselects the application of a new schedule. */
  domain?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Creates or edits an application's backup schedule: when it runs, how many of its backups it
 * keeps and for how long, and the destinations each backup is copied to. It is made of the same
 * fields every schedule of the console is made of (`policy/`: when, keep, send to, keep nothing
 * here), so a destination is a block of its own that wraps on a phone instead of a row of
 * four inputs that leave its name no room. Creating can also cover every application that has
 * no schedule yet, in one go. Editing sends the same shape through `PUT /{domain}`.
 */
export function ScheduleDialog({ existing, domain: preset0, open, onOpenChange }: ScheduleDialogProps) {
  const t = useT();
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const apps = useQuery({ ...appsQuery(), enabled: open && existing === undefined });
  const schedules = useQuery({ ...backupSchedulesQuery(), enabled: open });
  const serverDefault = schedules.data?.default_retention_count;
  const [domain, setDomain] = useState(existing?.domain ?? preset0 ?? "");
  // The stored expression first: "daily" is an alias whose time is not the form's default.
  const [schedule, setSchedule] = useState<ScheduleForm>(() => (existing !== undefined ? scheduleForm(existing.on_calendar !== "" ? existing.on_calendar : existing.schedule) : DEFAULT_SCHEDULE));
  // An existing schedule's retention is shown as it is: a schedule adopted from a 2.1 timer has
  // none of its own, and pre-filling 7/30 here is how saving it used to prune most of its backups.
  const [retentionMode, setRetentionMode] = useState<RetentionMode>(
    existing !== undefined && (existing.retention_count ?? null) === null && (existing.retention_days ?? null) === null ? "server" : "own",
  );
  const [retentionCount, setRetentionCount] = useState(existing ? limitText(existing.retention_count) : String(NEW_SCHEDULE_KEEP));
  const [retentionDays, setRetentionDays] = useState(existing ? limitText(existing.retention_days) : String(NEW_SCHEDULE_MAX_AGE));
  const [retentionGb, setRetentionGb] = useState(existing ? limitText(existing.retention_gb) : "");
  const [includeDatabases, setIncludeDatabases] = useState(existing?.include_databases ?? true);
  // Read from the schedule and given back: a schedule set to keep nothing here from the terminal
  // must not be turned back into one that does by saving it in the console.
  const [remoteOnly, setRemoteOnly] = useState(existing?.remote_only ?? false);
  const [sends, setSends] = useState<DestinationDraft[]>(
    (existing?.destinations ?? []).map((entry) => ({
      name: entry.name,
      count: limitText(entry.retention_count),
      days: limitText(entry.retention_days),
      gb: limitText(entry.retention_gb),
    })),
  );
  const [errors, setErrors] = useState<Partial<Record<ErrorKey, string>>>({});
  const { createSchedule, createSchedules, updateSchedule } = useBackupActions();
  const everyApp = existing === undefined && domain === EVERY_APP;
  const save = existing !== undefined ? updateSchedule : everyApp ? createSchedules : createSchedule;

  const scheduled = new Set((schedules.data?.schedules ?? []).map((entry) => entry.domain));
  const unscheduled = (apps.data?.apps ?? []).map((app) => app.domain).filter((name) => !scheduled.has(name));

  const close = (next: boolean): void => {
    if (!next && save.isPending) return;
    onOpenChange(next);
    if (!next) {
      save.reset();
      setErrors({});
    }
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    // Pressing says what is missing and goes to it, instead of a button that stays grey.
    const found: Partial<Record<ErrorKey, string>> = {};
    const targetDomain = existing?.domain ?? domain.trim();
    if (targetDomain === "") found.domain = t("backups.fields.chooseApplication");
    if (schedule.frequency === "custom" && schedule.custom.trim() === "") found.schedule = t("databases.policy.customRequired");
    if (schedule.frequency !== "hourly" && schedule.frequency !== "custom" && !validTime(schedule.time)) found.time = t("databases.policy.timeInvalid");
    if (retentionMode === "own") {
      if (!validLimit(retentionCount, MAX_COUNT)) found.count = t("databases.policy.countInvalid");
      if (!validLimit(retentionDays, MAX_DAYS)) found.days = t("databases.policy.daysInvalid");
      if (!validGb(retentionGb)) found.gb = t("backups.scheduleDialog.keepGbInvalid");
    }
    if (!destinationsValid(sends)) found.destinations = t("databases.policy.destinationLimitInvalid");
    setErrors(found);
    if (Object.keys(found).length > 0) {
      requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    const input = (name: string): CreateScheduleInput => ({
      domain: name,
      schedule: scheduleExpression(schedule),
      retentionCount: retentionMode === "server" ? null : (optionalNumber(retentionCount) ?? null),
      retentionDays: retentionMode === "server" ? null : (optionalNumber(retentionDays) ?? null),
      retentionGb: retentionMode === "server" ? null : gbValue(retentionGb),
      includeDatabases,
      remoteOnly,
      destinations: destinationInput(sends),
    });
    if (everyApp) {
      createSchedules.mutate(unscheduled.map(input), { onSuccess: () => close(false) });
      return;
    }
    (existing !== undefined ? updateSchedule : createSchedule).mutate(input(targetDomain), { onSuccess: () => close(false) });
  };

  const domainOptions = [
    ...(unscheduled.length > 1 ? [{ value: EVERY_APP, label: t("backups.scheduleDialog.everyApp", { count: unscheduled.length }) }] : []),
    ...(apps.data?.apps ?? []).map((app) => ({ value: app.domain, label: app.domain })),
  ];

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={existing ? t("backups.scheduleDialog.titleEdit", { domain: existing.domain }) : t("backups.scheduleDialog.titleNew")}
      description={t("backups.scheduleDialog.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close(false)}>
            {t("backups.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {existing ? t("backups.common.save") : t("backups.scheduleDialog.create")}
          </Button>
        </>
      }
    >
      <form id={formId} ref={formRef} noValidate onSubmit={submit} className="flex flex-col gap-6">
        {existing === undefined ? (
          <Field label={t("backups.fields.application")} nativeLabel={false} error={errors.domain}>
            <Select
              aria-label={t("backups.fields.application")}
              value={domain}
              onValueChange={(next) => {
                setDomain(next);
                setErrors((previous) => {
                  const next = { ...previous };
                  delete next.domain;
                  return next;
                });
              }}
              placeholder={apps.isPending ? t("backups.fields.loadingApplications") : t("backups.fields.chooseApplication")}
              options={domainOptions}
              disabled={apps.isPending || domainOptions.length === 0}
            />
          </Field>
        ) : null}

        <ScheduleFields value={schedule} onChange={setSchedule} scheduleError={errors.schedule} timeError={errors.time} />

        <Field
          label={t("backups.scheduleDialog.retention.label")}
          nativeLabel={false}
          description={
            existing === undefined
              ? t("backups.scheduleDialog.retention.newDescription", { keep: NEW_SCHEDULE_KEEP, days: NEW_SCHEDULE_MAX_AGE })
              : retentionMode === "server"
                ? t("backups.scheduleDialog.retention.serverDescription")
                : undefined
          }
        >
          <Select
            aria-label={t("backups.scheduleDialog.retention.label")}
            value={retentionMode}
            onValueChange={(next) => setRetentionMode(next === "server" ? "server" : "own")}
            options={[
              {
                value: "server",
                label:
                  serverDefault !== undefined
                    ? t("backups.scheduleDialog.retention.serverOption", { count: serverDefault })
                    : t("backups.scheduleDialog.retention.serverOptionUnknown"),
              },
              { value: "own", label: t("backups.scheduleDialog.retention.ownOption") },
            ]}
          />
        </Field>
        {retentionMode === "own" ? (
          <KeepFields
            words={{
              legend: t("backups.scheduleDialog.retention.ownOption"),
              count: t("backups.scheduleDialog.keepLabel"),
              countHelp: t("backups.scheduleDialog.keepDescription"),
              days: t("backups.scheduleDialog.maxAgeLabel"),
              daysHelp: t("backups.scheduleDialog.maxAgeDescription"),
              note: t("backups.scheduleDialog.retention.ownDescription"),
            }}
            count={retentionCount}
            days={retentionDays}
            onCount={setRetentionCount}
            onDays={setRetentionDays}
            countError={errors.count}
            daysError={errors.days}
          >
            <GbField label={t("backups.scheduleDialog.keepGbLabel")} help={t("backups.scheduleDialog.keepGbDescription")} value={retentionGb} onChange={setRetentionGb} error={errors.gb} />
          </KeepFields>
        ) : null}
        <Checkbox checked={includeDatabases} onCheckedChange={setIncludeDatabases} label={t("backups.scheduleDialog.dumpDatabases")} />

        <SendToFields
          enabled={open}
          words={{
            legend: t("backups.scheduleDialog.destinationsLegend"),
            none: t("backups.scheduleDialog.noDestinations"),
            noneHint: t("backups.scheduleDialog.noDestinationsHint"),
            failed: t("backups.destinations.loadError"),
            keepAll: t("databases.policy.keepAll"),
            countSuffix: t("backups.scheduleDialog.backupsSuffix"),
            daysSuffix: t("databases.policy.daysSuffix"),
            countFor: (name) => t("backups.scheduleDialog.keepOn", { name }),
            daysFor: (name) => t("backups.scheduleDialog.maxAgeOn", { name }),
            gbFor: (name) => t("backups.scheduleDialog.keepGbOn", { name }),
          }}
          value={sends}
          onChange={setSends}
          error={errors.destinations}
        />
        <RemoteOnlyField
          words={{
            label: t("backups.scheduleDialog.remoteOnly"),
            help: t("backups.scheduleDialog.remoteOnlyHelp"),
            needsDestination: t("backups.scheduleDialog.remoteOnlyNeedsDestination"),
          }}
          value={remoteOnly}
          onChange={setRemoteOnly}
          destinations={sends.length}
        />

        {save.isError ? (
          <ErrorBlock live compact error={save.error} title={everyApp ? t("backups.scheduleDialog.someFailed") : t("backups.scheduleDialog.error")} />
        ) : null}
      </form>
    </Dialog>
  );
}
