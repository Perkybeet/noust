import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { appsQuery } from "../../api/queries/apps";
import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import type { BackupSchedule } from "../../api/queries/backups";
import { backupSchedulesQuery } from "../../api/queries/backups";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import type { CreateScheduleInput, ScheduleDestinationInput } from "./useBackupActions";
import { useBackupActions } from "./useBackupActions";

/** The application choice that schedules every application without a schedule at once. */
export const EVERY_APP = "*";

function presets(t: T): { value: string; label: string }[] {
  return [
    { value: "hourly", label: t("backups.scheduleDialog.presets.hourly") },
    { value: "daily", label: t("backups.scheduleDialog.presets.daily") },
    { value: "weekly", label: t("backups.scheduleDialog.presets.weekly") },
    { value: "monthly", label: t("backups.scheduleDialog.presets.monthly") },
    { value: "custom", label: t("backups.scheduleDialog.presets.custom") },
  ];
}

const KNOWN = new Set(["hourly", "daily", "weekly", "monthly"]);

/** What a new schedule keeps unless the operator changes it; the API's own defaults too. */
const NEW_SCHEDULE_KEEP = 7;
const NEW_SCHEDULE_MAX_AGE = 30;

type RetentionMode = "server" | "own";

/** A retention limit as the form holds it: blank for "none", which the API takes as null. */
function limitText(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

/** A number typed for an optional field: blank stays unset rather than becoming 0. */
function optionalNumber(value: string): number | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * One destination this schedule copies to, with its own optional limits. The column's words
 * are said once above the rows; each input's accessible name names the destination too, so two
 * rows' inputs are never the same to a screen reader.
 */
function DestinationRow({
  entry,
  onChange,
  onRemove,
  disabled,
}: {
  entry: ScheduleDestinationInput;
  onChange: (next: ScheduleDestinationInput) => void;
  onRemove: () => void;
  disabled: boolean;
}) {
  const t = useT();
  return (
    <li className="flex items-center gap-3 rounded-control border border-border bg-bg-sunken px-3 py-2">
      <Mono truncate className="min-w-0 flex-1 text-13">
        {entry.name}
      </Mono>
      <Input
        aria-label={t("backups.scheduleDialog.keepOn", { name: entry.name })}
        size="sm"
        mono
        inputMode="numeric"
        value={entry.retentionCount !== undefined ? String(entry.retentionCount) : ""}
        onValueChange={(next: string) => onChange({ ...entry, retentionCount: optionalNumber(next) })}
        disabled={disabled}
        className="w-20 shrink-0"
      />
      <Input
        aria-label={t("backups.scheduleDialog.maxAgeOn", { name: entry.name })}
        size="sm"
        mono
        inputMode="numeric"
        value={entry.retentionDays !== undefined ? String(entry.retentionDays) : ""}
        onValueChange={(next: string) => onChange({ ...entry, retentionDays: optionalNumber(next) })}
        disabled={disabled}
        className="w-20 shrink-0"
      />
      <IconButton
        label={t("backups.scheduleDialog.removeDestination", { name: entry.name })}
        icon={<ICONS.close />}
        size="sm"
        onClick={onRemove}
        disabled={disabled}
      />
    </li>
  );
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
 * Creates or edits an application's backup schedule: when it runs (a preset or a calendar
 * expression), how many of its backups it keeps, and any destinations each backup is copied
 * to. Creating can also cover every application that has no schedule yet, in one go. Editing
 * sends the same shape through `PUT /{domain}` instead of posting again.
 */
export function ScheduleDialog({ existing, domain: preset0, open, onOpenChange }: ScheduleDialogProps) {
  const t = useT();
  const formId = useId();
  const apps = useQuery({ ...appsQuery(), enabled: open && existing === undefined });
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: open });
  const schedules = useQuery({ ...backupSchedulesQuery(), enabled: open });
  const serverDefault = schedules.data?.default_retention_count;
  const [domain, setDomain] = useState(existing?.domain ?? preset0 ?? "");
  const [preset, setPreset] = useState(existing !== undefined && KNOWN.has(existing.schedule) ? existing.schedule : "daily");
  const [customCalendar, setCustomCalendar] = useState(existing?.on_calendar ?? "");
  // An existing schedule's retention is shown as it is: a schedule adopted from a 2.1 timer has
  // none of its own, and pre-filling 7/30 here is how saving it used to prune most of its backups.
  const [retentionMode, setRetentionMode] = useState<RetentionMode>(
    existing !== undefined && (existing.retention_count ?? null) === null && (existing.retention_days ?? null) === null ? "server" : "own",
  );
  const [retentionCount, setRetentionCount] = useState(existing ? limitText(existing.retention_count) : String(NEW_SCHEDULE_KEEP));
  const [retentionDays, setRetentionDays] = useState(existing ? limitText(existing.retention_days) : String(NEW_SCHEDULE_MAX_AGE));
  const [includeDatabases, setIncludeDatabases] = useState(existing?.include_databases ?? true);
  const [scheduleDestinations, setScheduleDestinations] = useState<ScheduleDestinationInput[]>(
    (existing?.destinations ?? []).map((entry) => ({
      name: entry.name,
      retentionCount: entry.retention_count ?? undefined,
      retentionDays: entry.retention_days ?? undefined,
    })),
  );
  const [destinationToAdd, setDestinationToAdd] = useState("");
  const { createSchedule, createSchedules, updateSchedule } = useBackupActions();
  const everyApp = existing === undefined && domain === EVERY_APP;
  const save = existing !== undefined ? updateSchedule : everyApp ? createSchedules : createSchedule;

  const scheduled = new Set((schedules.data?.schedules ?? []).map((entry) => entry.domain));
  const unscheduled = (apps.data?.apps ?? []).map((app) => app.domain).filter((name) => !scheduled.has(name));

  const close = (next: boolean): void => {
    if (!next && save.isPending) return;
    onOpenChange(next);
    if (!next) save.reset();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const targetDomain = existing?.domain ?? domain.trim();
    if (targetDomain === "") return;
    const schedule = preset === "custom" ? customCalendar.trim() : preset;
    if (schedule === "") return;
    const input = (name: string): CreateScheduleInput => ({
      domain: name,
      schedule,
      retentionCount: retentionMode === "server" ? null : (optionalNumber(retentionCount) ?? null),
      retentionDays: retentionMode === "server" ? null : (optionalNumber(retentionDays) ?? null),
      includeDatabases,
      destinations: scheduleDestinations,
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
  const addedNames = new Set(scheduleDestinations.map((entry) => entry.name));
  const addableOptions = (destinations.data?.destinations ?? [])
    .filter((entry) => !addedNames.has(entry.name))
    .map((entry) => ({ value: entry.name, label: entry.name }));
  const noDestinations = destinations.data?.destinations.length === 0;

  const addDestination = (): void => {
    if (destinationToAdd === "") return;
    setScheduleDestinations((current) => [...current, { name: destinationToAdd }]);
    setDestinationToAdd("");
  };

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
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={save.isPending}
            disabled={(existing === undefined && domain.trim() === "") || (preset === "custom" && customCalendar.trim() === "")}
          >
            {existing ? t("backups.common.save") : t("backups.scheduleDialog.create")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-5">
        <div className="grid gap-5 sm:grid-cols-2">
          {existing === undefined ? (
            <Field label={t("backups.fields.application")} nativeLabel={false}>
              <Select
                aria-label={t("backups.fields.application")}
                value={domain}
                onValueChange={setDomain}
                placeholder={apps.isPending ? t("backups.fields.loadingApplications") : t("backups.fields.chooseApplication")}
                options={domainOptions}
                disabled={apps.isPending || domainOptions.length === 0}
              />
            </Field>
          ) : null}
          <Field label={t("backups.scheduleDialog.scheduleLabel")} name="schedule" nativeLabel={false}>
            <Select aria-label={t("backups.scheduleDialog.scheduleLabel")} value={preset} onValueChange={setPreset} options={presets(t)} />
          </Field>
        </div>
        {preset === "custom" ? (
          <Field label={t("backups.scheduleDialog.calendarLabel")} description={t("backups.scheduleDialog.calendarDescription")}>
            <Input mono value={customCalendar} onValueChange={setCustomCalendar} placeholder="*-*-* 03:30:00" autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        <Field
          label={t("backups.scheduleDialog.retention.label")}
          nativeLabel={false}
          description={
            existing === undefined
              ? t("backups.scheduleDialog.retention.newDescription", { keep: NEW_SCHEDULE_KEEP, days: NEW_SCHEDULE_MAX_AGE })
              : retentionMode === "server"
                ? t("backups.scheduleDialog.retention.serverDescription")
                : t("backups.scheduleDialog.retention.ownDescription")
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
          <div className="grid grid-cols-2 gap-5">
            <Field label={t("backups.scheduleDialog.keepLabel")} description={t("backups.scheduleDialog.keepDescription")}>
              <Input mono inputMode="numeric" value={retentionCount} onValueChange={setRetentionCount} />
            </Field>
            <Field label={t("backups.scheduleDialog.maxAgeLabel")} description={t("backups.scheduleDialog.maxAgeDescription")}>
              <Input mono inputMode="numeric" value={retentionDays} onValueChange={setRetentionDays} />
            </Field>
          </div>
        ) : null}
        <Checkbox checked={includeDatabases} onCheckedChange={setIncludeDatabases} label={t("backups.scheduleDialog.dumpDatabases")} />

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-1 text-13 font-medium text-fg">{t("backups.scheduleDialog.destinationsLegend")}</legend>
          <p className="text-12 text-fg-muted">
            {noDestinations ? t("backups.scheduleDialog.noDestinations") : t("backups.scheduleDialog.destinationsHint")}
          </p>
          {scheduleDestinations.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              {/* The columns' words, once: the inputs below carry them with each destination's name. */}
              <div aria-hidden="true" className="flex items-center gap-3 px-3 text-12 text-fg-muted">
                <span className="flex-1">{t("backups.fields.destination")}</span>
                <span className="w-20 shrink-0">{t("backups.scheduleDialog.keepLabel")}</span>
                <span className="w-20 shrink-0">{t("backups.scheduleDialog.maxAgeShort")}</span>
                <span className="size-control-sm shrink-0" />
              </div>
            <ul className="flex flex-col gap-2">
              {scheduleDestinations.map((entry) => (
                <DestinationRow
                  key={entry.name}
                  entry={entry}
                  disabled={save.isPending}
                  onChange={(next) => {
                    setScheduleDestinations((current) => current.map((item) => (item.name === next.name ? next : item)));
                  }}
                  onRemove={() => {
                    setScheduleDestinations((current) => current.filter((item) => item.name !== entry.name));
                  }}
                />
              ))}
            </ul>
            </div>
          ) : null}
          {noDestinations ? null : (
            <div className="flex items-center gap-2">
              <Select
                aria-label={t("backups.scheduleDialog.addDestinationAria")}
                value={destinationToAdd}
                onValueChange={setDestinationToAdd}
                placeholder={destinations.isPending ? t("backups.fields.loadingDestinations") : t("backups.scheduleDialog.chooseDestinationToAdd")}
                options={addableOptions}
                disabled={save.isPending || destinations.isPending || addableOptions.length === 0}
                size="sm"
              />
              <Button size="sm" type="button" icon={<ICONS.add aria-hidden="true" />} disabled={destinationToAdd === "" || save.isPending} onClick={addDestination}>
                {t("backups.common.add")}
              </Button>
            </div>
          )}
        </fieldset>

        {save.isError ? (
          <ErrorBlock live compact error={save.error} title={everyApp ? t("backups.scheduleDialog.someFailed") : t("backups.scheduleDialog.error")} />
        ) : null}
      </form>
    </Dialog>
  );
}
