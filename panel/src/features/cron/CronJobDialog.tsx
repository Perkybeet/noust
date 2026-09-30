import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError } from "../../api/client";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { CronJob, Schedule } from "./data";
import { calendarWords, runTimes, schedulePresets } from "./data";
import type { CreateCronJobBody } from "./useCronActions";
import { useCronActions } from "./useCronActions";
import { useCronPreview } from "./useCronPreview";

export interface CronJobDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present to rewrite an existing job; absent to create one. */
  job?: CronJob;
}

/**
 * The refusal `POST /api/cron/preview` answered with, in the shape that fits: a schedule too
 * short, too long, or built from the wrong characters never reaches systemd - the request
 * model's own field validator refuses it first, as a 422 whose per-field message carries the
 * words (the response's `detail` is only ever the generic "Validation failed" for that shape);
 * an expression systemd itself rejects raises past the request model, and `detail`/`hint` carry
 * its own message and output instead.
 */
function schedulePreviewError(error: unknown): { message: string; output: string | null } | null {
  if (!isApiError(error)) return null;
  const fieldMessage = error.fields?.["schedule"];
  if (fieldMessage !== undefined) return { message: fieldMessage, output: null };
  return { message: error.detail, output: error.hint };
}

/**
 * The schedule in words, its calendar as systemd normalised it, and its next runs: on the
 * server's clock first, since that is the clock the expression is written in, then on the
 * reader's own when it differs. Or, when the schedule is refused, why, in systemd's words.
 */
function SchedulePreview({ schedule }: { schedule: string }) {
  const t = useT();
  const preview = useCronPreview(schedule);

  if (schedule.trim() === "") return null;

  if (preview.isError) {
    const refusal = schedulePreviewError(preview.error) ?? { message: t("cron.dialog.previewCheckError"), output: null };
    return (
      <div role="alert" className="flex flex-col gap-1.5">
        <p className="text-13 text-fail">{refusal.message}</p>
        {refusal.output !== null && refusal.output.trim() !== "" ? (
          <SystemOutput label={t("cron.dialog.systemdOutputLabel")} maxHeight="max-h-28">
            {refusal.output}
          </SystemOutput>
        ) : null}
      </div>
    );
  }

  if (preview.data === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-1.5 rounded-control border border-border bg-bg-sunken p-3">
        <span className="sr-only">{t("cron.dialog.checkingSchedule")}</span>
        <Skeleton className="h-3.5 w-48" />
        <Skeleton className="h-3.5 w-64" />
        <Skeleton className="h-3.5 w-56" />
      </div>
    );
  }

  const runs = preview.data.next_runs.map((run) => ({ run, times: runTimes(run, t.locale) }));
  const zone = runs[0]?.times.zone ?? "";
  return (
    <div className="flex flex-col gap-2.5 rounded-control border border-border bg-bg-sunken p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <p className="text-13 font-medium text-fg">{calendarWords(schedule, preview.data.calendar, t.locale)}</p>
        <Mono tone="muted" className="text-12">
          {preview.data.calendar}
        </Mono>
      </div>
      {runs.length === 0 ? (
        <p className="text-13 text-fg-muted">{t("cron.dialog.noFutureRun")}</p>
      ) : (
        <>
          {zone !== "" ? <p className="text-12 text-fg-muted">{t("cron.dialog.serverClock", { zone })}</p> : null}
          <ul aria-label={t("cron.dialog.nextRuns")} className="flex flex-col gap-1">
            {runs.map(({ run, times }) => (
              <li key={run} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-13">
                <span className="tabular-nums text-fg">{times.zone === "" ? times.server : t("cron.dialog.serverTime", { time: times.server, zone: times.zone })}</span>
                <span className="flex items-baseline gap-1.5 text-12 text-fg-muted tabular-nums">
                  {times.local !== null ? <span>{t("cron.dialog.yourTime", { time: times.local })}</span> : null}
                  <RelativeTime value={run} />
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/**
 * Creates a cron job, or rewrites one Noust already owns - `POST /api/cron` does both. The
 * schedule previews live through `POST /api/cron/preview` as it is typed or a preset is
 * chosen (`SchedulePreview`, debounced by `useCronPreview`): in words and on both clocks when
 * it validates, systemd's own refusal when it does not. A missing name or command is said
 * where it is missing when the operator tries to save, rather than by a disabled button.
 */
export function CronJobDialog({ open, onOpenChange, job }: CronJobDialogProps) {
  const t = useT();
  const editing = job !== undefined;
  const { create } = useCronActions();
  const [name, setName] = useState(job?.name ?? "");
  const [command, setCommand] = useState(job?.command ?? "");
  const [preset, setPreset] = useState<Schedule>((job?.schedule as Schedule | undefined) ?? "daily");
  const [calendar, setCalendar] = useState(job?.on_calendar ?? "");
  const [user, setUser] = useState(job?.user ?? "");
  const [workingDirectory, setWorkingDirectory] = useState(job?.working_directory ?? "");
  const [submitted, setSubmitted] = useState(false);
  const formId = useId();

  const close = (next: boolean): void => {
    if (!next && create.isPending) return;
    onOpenChange(next);
    if (!next) create.reset();
  };

  // The alias travels as-is: CronManager expands hourly/daily/weekly/monthly itself
  // (SCHEDULE_ALIASES), so the dialog does not keep its own copy of what each one means.
  const schedule = preset === "custom" ? calendar.trim() : preset;
  const missing = {
    name: name.trim() === "" ? t("cron.fields.nameMissing") : null,
    command: command.trim() === "" ? t("cron.fields.commandMissing") : null,
    calendar: preset === "custom" && schedule === "" ? t("cron.fields.calendarMissing") : null,
  };
  const valid = missing.name === null && missing.command === null && missing.calendar === null;
  const serverError = (field: string): string | undefined => (isApiError(create.error) ? create.error.fields?.[field] : undefined);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (!valid || create.isPending) return;
    const body: CreateCronJobBody = {
      name: name.trim(),
      command: command.trim(),
      schedule,
      ...(user.trim() !== "" ? { user: user.trim() } : {}),
      ...(workingDirectory.trim() !== "" ? { working_directory: workingDirectory.trim() } : {}),
      // Saving an existing job rewrites it: without its application it would lose the link
      // (and the working directory default that follows the app's layout).
      ...(job?.app_domain ? { app_domain: job.app_domain } : {}),
    };
    create.mutate(body, { onSuccess: () => close(false) });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={editing ? t("cron.dialog.titleEdit", { name: job.name }) : t("cron.dialog.titleNew")}
      description={t("cron.dialog.description")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => close(false)}>
            {t("cron.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending}>
            {editing ? t("cron.dialog.saveJob") : t("cron.dialog.createJob")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <Field label={t("cron.fields.name")} name="name" description={t("cron.fields.nameDescription")} error={(submitted ? missing.name : null) ?? serverError("name")}>
          <Input value={name} onValueChange={setName} mono disabled={editing} placeholder="nightly-report" autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("cron.fields.command")} name="command" description={t("cron.fields.commandDescription")} error={(submitted ? missing.command : null) ?? serverError("command")}>
          <Input value={command} onValueChange={setCommand} mono placeholder="/usr/bin/noust backup create example.com" autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("cron.fields.schedule")} name="schedule" nativeLabel={false}>
          <Select value={preset} onValueChange={setPreset} options={schedulePresets(t.locale)} />
        </Field>
        {preset === "custom" ? (
          <Field label={t("cron.fields.calendarLabel")} name="calendar" description={t("cron.fields.calendarDescription")} error={submitted ? missing.calendar : null}>
            <Input value={calendar} onValueChange={setCalendar} mono placeholder="Mon..Fri *-*-* 09:00:00" autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        <SchedulePreview schedule={schedule} />
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label={t("cron.fields.user")} name="user" optional description={t("cron.fields.userDescription")}>
            <Input value={user} onValueChange={setUser} mono autoComplete="off" spellCheck={false} />
          </Field>
          <Field label={t("cron.fields.workingDirectory")} name="working_directory" optional description={t("cron.fields.workingDirectoryDescription")}>
            <Input value={workingDirectory} onValueChange={setWorkingDirectory} mono autoComplete="off" spellCheck={false} />
          </Field>
        </div>
        {create.isError ? (
          <ErrorBlock live compact error={create.error} title={editing ? t("cron.dialog.errorSave") : t("cron.dialog.errorCreate")} />
        ) : null}
      </form>
    </Dialog>
  );
}
