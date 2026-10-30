import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError } from "../../api/client";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { CronJob } from "./data";
import { SYSTEMD_DAYS, calendarWords, runTimes, weekdayName } from "./data";
import { choiceOf, scheduleOf, timeProblem } from "./schedule";
import type { Frequency, ScheduleChoice, Weekday } from "./schedule";
import type { CreateCronJobBody } from "./useCronActions";
import { useCronActions } from "./useCronActions";
import { useCronPreview } from "./useCronPreview";

/** The longest a task may run, in seconds: the API's own bound. */
const TIMEOUT_MAX = 86_400;
/** What the limit starts at when a person turns it on: an hour is long for most tasks and short for none that matter. */
const TIMEOUT_START = "3600";

const FREQUENCIES: readonly Frequency[] = ["hourly", "daily", "weekly", "monthly", "custom"];
const DAYS_OF_MONTH = Array.from({ length: 28 }, (_, index) => index + 1);

/** Why a time limit is not one the API takes, or null when it is. */
function timeoutProblem(text: string, message: string): string | null {
  const trimmed = text.trim();
  const seconds = /^\d+$/.test(trimmed) ? Number.parseInt(trimmed, 10) : Number.NaN;
  return seconds >= 1 && seconds <= TIMEOUT_MAX ? null : message;
}

export interface CronJobDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present to rewrite an existing task; absent to create one. */
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
      <LoadingRegion label={t("cron.dialog.checkingSchedule")} className="flex flex-col gap-1.5 rounded-control border border-border bg-bg-sunken p-3">
        <Skeleton className="h-3.5 w-48" />
        <Skeleton className="h-3.5 w-64" />
        <Skeleton className="h-3.5 w-56" />
      </LoadingRegion>
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
 * Creates a scheduled task, or rewrites one Noust already owns - `POST /api/cron` does both. How often it runs
 * is chosen the way a person says it (every day, at 03:30), and the console writes the calendar expression;
 * what the choices cannot say is a custom expression. The schedule previews live through `POST /api/cron/preview`
 * as it is chosen (`SchedulePreview`, debounced by `useCronPreview`): in words and on both clocks when it
 * validates, systemd's own refusal when it does not. A missing name or command is said where it is missing when
 * the operator tries to save, rather than by a disabled button. What most tasks never change (who runs it,
 * where, for how long) is folded under "More options".
 */
export function CronJobDialog({ open, onOpenChange, job }: CronJobDialogProps) {
  const t = useT();
  const editing = job !== undefined;
  const { create } = useCronActions();
  const [name, setName] = useState(job?.name ?? "");
  const [command, setCommand] = useState(job?.command ?? "");
  const [choice, setChoice] = useState<ScheduleChoice>(() => choiceOf(job ?? { schedule: "daily", on_calendar: "" }));
  const [user, setUser] = useState(job?.user ?? "");
  const [workingDirectory, setWorkingDirectory] = useState(job?.working_directory ?? "");
  // A limit is a choice to make, not a number that means "none" when it is left empty.
  const [limited, setLimited] = useState(job?.timeout != null);
  const [timeout, setTimeoutText] = useState(job?.timeout == null ? TIMEOUT_START : String(job.timeout));
  const [service, setService] = useState(job?.service ?? "");
  const [submitted, setSubmitted] = useState(false);
  const formId = useId();

  const close = (next: boolean): void => {
    if (!next && create.isPending) return;
    onOpenChange(next);
    if (!next) create.reset();
  };

  const set = (patch: Partial<ScheduleChoice>): void => setChoice((current) => ({ ...current, ...patch }));
  const schedule = scheduleOf(choice);
  const missing = {
    name: name.trim() === "" ? t("cron.fields.nameMissing") : null,
    command: command.trim() === "" ? t("cron.fields.commandMissing") : null,
    calendar: choice.frequency === "custom" && schedule === "" ? t("cron.fields.calendarMissing") : null,
    time: choice.frequency !== "hourly" && choice.frequency !== "custom" && timeProblem(choice.time) ? t("cron.fields.timeInvalid") : null,
    timeout: limited ? timeoutProblem(timeout, t("cron.fields.timeoutRange")) : null,
  };
  const valid = Object.values(missing).every((problem) => problem === null);
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
      ...(limited ? { timeout: Number.parseInt(timeout.trim(), 10) } : {}),
      ...(service.trim() !== "" ? { service: service.trim() } : {}),
      // Saving an existing task rewrites it: without its application it would lose the link
      // (and the working directory default that follows the app's layout).
      ...(job?.app_domain ? { app_domain: job.app_domain } : {}),
    };
    create.mutate(body, { onSuccess: () => close(false) });
  };

  const weekdays = SYSTEMD_DAYS.map((day) => ({ value: day, label: weekdayName(day, t.locale) }));
  const showTime = choice.frequency === "daily" || choice.frequency === "weekly" || choice.frequency === "monthly";
  const optionsDiffer = user !== "" || workingDirectory !== "" || limited || service !== "";
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={editing ? t("common.words.editNamed", { name: job.name }) : t("cron.dialog.titleNew")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => close(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending}>
            {editing ? t("cron.dialog.saveJob") : t("cron.dialog.createJob")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        <Field label={t("common.words.name")} name="name" description={t("cron.fields.nameDescription")} error={(submitted ? missing.name : null) ?? serverError("name")}>
          <Input value={name} onValueChange={setName} mono disabled={editing} placeholder="nightly-report" autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("cron.fields.command")} name="command" description={t("cron.fields.commandDescription")} error={(submitted ? missing.command : null) ?? serverError("command")}>
          <Input value={command} onValueChange={setCommand} mono placeholder="/usr/bin/noust backup create example.com" autoComplete="off" spellCheck={false} />
        </Field>
        <div className="flex flex-wrap items-start gap-4">
          <Field label={t("cron.fields.schedule")} name="schedule" nativeLabel={false} className="w-44">
            <Select value={choice.frequency} onValueChange={(frequency) => set({ frequency })} options={FREQUENCIES.map((value) => ({ value, label: t(`cron.frequency.${value}`) }))} />
          </Field>
          {choice.frequency === "weekly" ? (
            <Field label={t("cron.fields.weekday")} name="weekday" nativeLabel={false} className="w-40">
              <Select value={choice.weekday} onValueChange={(weekday: Weekday) => set({ weekday })} options={weekdays} />
            </Field>
          ) : null}
          {choice.frequency === "monthly" ? (
            <Field label={t("cron.fields.day")} name="day" nativeLabel={false} className="w-28">
              <Select value={String(choice.day)} onValueChange={(day) => set({ day: Number(day) })} options={DAYS_OF_MONTH.map((day) => ({ value: String(day), label: String(day) }))} />
            </Field>
          ) : null}
          {showTime ? (
            <Field label={t("cron.fields.time")} name="time" error={submitted ? missing.time : null} className="w-32">
              {/* Written as the console writes every hour, 24 hours and two digits: a browser's own time field would say AM and PM in one language and not in another. */}
              <Input value={choice.time} onValueChange={(time) => set({ time })} mono inputMode="numeric" maxLength={5} placeholder="03:30" autoComplete="off" spellCheck={false} />
            </Field>
          ) : null}
        </div>
        {choice.frequency === "custom" ? (
          <Field label={t("cron.fields.calendarLabel")} name="calendar" description={t("cron.fields.calendarDescription")} error={submitted ? missing.calendar : null}>
            <Input value={choice.calendar} onValueChange={(calendar) => set({ calendar })} mono placeholder="Mon..Fri *-*-* 09:00:00" autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        <SchedulePreview schedule={schedule} />
        <Disclosure label={t("cron.dialog.more")} defaultOpen={optionsDiffer}>
          <div className="flex flex-col gap-5 pt-2">
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label={t("cron.fields.user")} name="user" optional description={t("cron.fields.userDescription")}>
                <Input value={user} onValueChange={setUser} mono autoComplete="off" spellCheck={false} />
              </Field>
              <Field label={t("cron.fields.workingDirectory")} name="working_directory" optional description={t("cron.fields.workingDirectoryDescription")}>
                <Input value={workingDirectory} onValueChange={setWorkingDirectory} mono autoComplete="off" spellCheck={false} />
              </Field>
            </div>
            <div className="flex flex-col gap-3">
              <Checkbox label={t("cron.fields.limit")} description={t("cron.fields.limitDescription")} checked={limited} onCheckedChange={setLimited} />
              {limited ? (
                <Field label={t("cron.fields.timeout")} name="timeout" error={(submitted ? missing.timeout : null) ?? serverError("timeout")} className="w-44">
                  <Input value={timeout} onValueChange={setTimeoutText} mono inputMode="numeric" suffix="s" autoComplete="off" spellCheck={false} />
                </Field>
              ) : null}
            </div>
            {/* Only a task of an application can run in a service of its stack. */}
            {job?.app_domain ? (
              <Field label={t("cron.fields.service")} name="service" optional description={t("cron.fields.serviceDescription")} error={serverError("service")}>
                <Input value={service} onValueChange={setService} mono placeholder="web" autoComplete="off" spellCheck={false} />
              </Field>
            ) : null}
          </div>
        </Disclosure>
        {create.isError ? (
          <ErrorBlock live compact error={create.error} title={editing ? t("cron.dialog.errorSave") : t("cron.dialog.errorCreate")} />
        ) : null}
      </form>
    </Dialog>
  );
}
