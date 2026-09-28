import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { appsQuery } from "../../api/queries/apps";
import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import type { BackupSchedule } from "../../api/queries/backups";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import type { ScheduleDestinationInput } from "./useBackupActions";
import { useBackupActions } from "./useBackupActions";

const PRESETS = [
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily, 02:00" },
  { value: "weekly", label: "Weekly, Monday 02:00" },
  { value: "monthly", label: "Monthly, 1st at 02:00" },
  { value: "custom", label: "Custom (systemd OnCalendar)" },
];

const KNOWN = new Set(["hourly", "daily", "weekly", "monthly"]);

/** A number typed for an optional field: blank stays unset rather than becoming 0. */
function optionalNumber(value: string): number | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * One destination this schedule pushes to, with its own optional retention. The visible label
 * of each input stays the short "Keep" / "Max age" (the row already names the destination), but
 * its accessible name names the destination too - `Field` would otherwise give every row's
 * inputs the same name, indistinguishable to a screen reader or a test.
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
  const keepId = useId();
  const maxAgeId = useId();
  return (
    <div className="flex flex-wrap items-end gap-2 rounded-control border border-border bg-bg-sunken px-3 py-2">
      <span translate="no" className="mono min-w-0 flex-1 basis-28 self-center truncate text-13 text-fg">
        {entry.name}
      </span>
      <div className="flex w-20 shrink-0 flex-col gap-1">
        <label htmlFor={keepId} className="text-12 text-fg-muted">
          Keep
        </label>
        <Input
          id={keepId}
          aria-label={`Keep on ${entry.name}`}
          size="sm"
          mono
          inputMode="numeric"
          value={entry.retentionCount !== undefined ? String(entry.retentionCount) : ""}
          onValueChange={(next: string) => onChange({ ...entry, retentionCount: optionalNumber(next) })}
          disabled={disabled}
        />
      </div>
      <div className="flex w-20 shrink-0 flex-col gap-1">
        <label htmlFor={maxAgeId} className="text-12 text-fg-muted">
          Max age
        </label>
        <Input
          id={maxAgeId}
          aria-label={`Max age on ${entry.name}`}
          size="sm"
          mono
          inputMode="numeric"
          value={entry.retentionDays !== undefined ? String(entry.retentionDays) : ""}
          onValueChange={(next: string) => onChange({ ...entry, retentionDays: optionalNumber(next) })}
          disabled={disabled}
        />
      </div>
      <IconButton label={`Remove ${entry.name}`} icon={<X aria-hidden="true" />} size="sm" onClick={onRemove} disabled={disabled} />
    </div>
  );
}

export interface ScheduleDialogProps {
  /** Present to edit a schedule; absent to create one. */
  existing?: BackupSchedule;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Creates or edits an application's backup schedule: a preset or a raw systemd calendar
 * expression, local retention, and any remote destinations to push each backup to. Creating
 * posts once; editing sends the same shape through `PUT /{domain}` instead of posting again.
 */
export function ScheduleDialog({ existing, open, onOpenChange }: ScheduleDialogProps) {
  const formId = useId();
  const apps = useQuery({ ...appsQuery(), enabled: open && existing === undefined });
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: open });
  const [domain, setDomain] = useState(existing?.domain ?? "");
  const [preset, setPreset] = useState(existing !== undefined && KNOWN.has(existing.schedule) ? existing.schedule : "daily");
  const [customCalendar, setCustomCalendar] = useState(existing?.on_calendar ?? "");
  const [retentionCount, setRetentionCount] = useState(String(existing?.retention_count ?? 7));
  const [retentionDays, setRetentionDays] = useState(String(existing?.retention_days ?? 30));
  const [includeDatabases, setIncludeDatabases] = useState(true);
  const [scheduleDestinations, setScheduleDestinations] = useState<ScheduleDestinationInput[]>(
    (existing?.destinations ?? []).map((entry) => ({
      name: entry.name,
      retentionCount: entry.retention_count ?? undefined,
      retentionDays: entry.retention_days ?? undefined,
    })),
  );
  const [destinationToAdd, setDestinationToAdd] = useState("");
  const { createSchedule, updateSchedule } = useBackupActions();
  const save = existing === undefined ? createSchedule : updateSchedule;

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
    save.mutate(
      {
        domain: targetDomain,
        schedule,
        retentionCount: Number(retentionCount) || 7,
        retentionDays: Number(retentionDays) || 30,
        includeDatabases,
        destinations: scheduleDestinations,
      },
      { onSuccess: () => close(false) },
    );
  };

  const domainOptions = (apps.data?.apps ?? []).map((app) => ({ value: app.domain, label: app.domain }));
  const addedNames = new Set(scheduleDestinations.map((entry) => entry.name));
  const addableOptions = (destinations.data?.destinations ?? [])
    .filter((entry) => !addedNames.has(entry.name))
    .map((entry) => ({ value: entry.name, label: entry.name }));

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
      title={existing ? `Edit the schedule for ${existing.domain}` : "New backup schedule"}
      description="Backs this application up on a systemd timer, with its own retention."
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close(false)}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={save.isPending}
            disabled={(existing === undefined && domain.trim() === "") || (preset === "custom" && customCalendar.trim() === "")}
          >
            {existing ? "Save" : "Create schedule"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
        {existing === undefined ? (
          <Field label="Application" nativeLabel={false}>
            <Select
              aria-label="Application"
              value={domain}
              onValueChange={setDomain}
              placeholder={apps.isPending ? "Loading applications..." : "Choose an application"}
              options={domainOptions}
              disabled={apps.isPending || domainOptions.length === 0}
            />
          </Field>
        ) : null}
        <Field label="Schedule" name="schedule" nativeLabel={false}>
          <Select aria-label="Schedule" value={preset} onValueChange={setPreset} options={PRESETS} />
        </Field>
        {preset === "custom" ? (
          <Field label="OnCalendar expression" description="Systemd calendar syntax, e.g. *-*-* 03:30:00.">
            <Input mono value={customCalendar} onValueChange={setCustomCalendar} placeholder="*-*-* 03:30:00" autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Keep" description="Backups to keep locally.">
            <Input mono inputMode="numeric" value={retentionCount} onValueChange={setRetentionCount} />
          </Field>
          <Field label="Max age" description="Days before a local backup is pruned.">
            <Input mono inputMode="numeric" value={retentionDays} onValueChange={setRetentionDays} />
          </Field>
        </div>
        <Checkbox checked={includeDatabases} onCheckedChange={setIncludeDatabases} label="Dump databases too" />

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-13 font-medium text-fg">Destinations</legend>
          <p className="text-12 text-fg-muted">
            Push each backup on to one or more remote destinations too. Retention here is optional and per destination; leave it blank to
            keep every copy sent there.
          </p>
          {scheduleDestinations.length > 0 ? (
            <div className="flex flex-col gap-2">
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
            </div>
          ) : null}
          <div className="flex items-center gap-2">
            <Select
              aria-label="Add a destination"
              value={destinationToAdd}
              onValueChange={setDestinationToAdd}
              placeholder={destinations.isPending ? "Loading destinations..." : "Choose a destination to add"}
              options={addableOptions}
              disabled={save.isPending || destinations.isPending || addableOptions.length === 0}
              size="sm"
            />
            <Button
              size="sm"
              type="button"
              icon={<Plus aria-hidden="true" />}
              disabled={destinationToAdd === "" || save.isPending}
              onClick={addDestination}
            >
              Add
            </Button>
          </div>
        </fieldset>

        {save.isError ? <ErrorBlock live compact error={save.error} title="The schedule was not saved" /> : null}
      </form>
    </Dialog>
  );
}
