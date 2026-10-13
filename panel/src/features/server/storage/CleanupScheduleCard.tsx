/**
 * What a server cleans by itself: the policy (when, how full a disk must be, which actions), the
 * timer that runs it, and a way to run it now. Saving a policy writes or removes the timer, in
 * sudo mode. The two Docker actions delete something an operator may want back, so a policy that
 * runs them says so once, in a box the form will not let you skip.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { ErrorBlock } from "../../../components/page/QueryState";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { calendarWords } from "../../cron/data";
import { ScheduleFields } from "../../backups/policy/ScheduleFields";
import { scheduleExpression } from "../../databases/backups/schedule";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock } from "../errors";
import { cleanupScheduleQuery, serverKeys } from "../queries";
import type { CleanupPolicy, CleanupSchedule } from "../queries";
import { useServerJob } from "../serverJob";
import { EMPTY_DRAFT, JOURNAL_MAX_MB, JOURNAL_MIN_MB, draftOf, policyBody, policyProblems, runsDocker } from "./cleanupPolicy";
import type { PolicyDraft, PolicyProblem } from "./cleanupPolicy";

/** What a scheduled action is called; an action this console does not know keeps its id. */
export function cleanupActionLabel(t: T, action: string): string {
  switch (action) {
    case "journal":
      return t("server.storage.candidates.journal");
    case "pkg-cache":
      return t("server.storage.candidates.pkgCache");
    case "docker-build-cache":
      return t("server.storage.candidates.dockerBuildCache");
    case "docker-dangling-images":
      return t("server.storage.schedule.actions.dockerDangling");
    case "releases":
      return t("server.storage.candidates.releases");
    default:
      return action;
  }
}

/** What a scheduled action takes, in a sentence under its checkbox. */
function cleanupActionHelp(t: T, action: string): string | undefined {
  switch (action) {
    case "journal":
      return t("server.storage.schedule.actions.journalHelp");
    case "pkg-cache":
      return t("server.storage.schedule.actions.pkgCacheHelp");
    case "docker-build-cache":
      return t("server.storage.schedule.actions.dockerBuildCacheHelp");
    case "docker-dangling-images":
      return t("server.storage.schedule.actions.dockerDanglingHelp");
    case "releases":
      return t("server.storage.schedule.actions.releasesHelp");
    default:
      return undefined;
  }
}

/** The schedule of a stored policy in plain words: "Every day at 02:00". */
export function policyWhen(t: T, policy: CleanupPolicy): string {
  const form = draftOf(policy).schedule;
  return form.frequency === "custom" ? policy.schedule : calendarWords(form.frequency, scheduleExpression(form), t.locale);
}

interface EditorProps {
  schedule: CleanupSchedule;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function PolicyEditor({ schedule, open, onOpenChange }: EditorProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const stored = schedule.policy;
  // A server that has never set a policy starts from the defaults, switched on.
  const configured = schedule.timer.installed || stored.enabled;
  const [draft, setDraft] = useState<PolicyDraft>(() => (configured ? draftOf(stored) : EMPTY_DRAFT));
  const [tried, setTried] = useState(false);
  const problems = tried ? policyProblems(draft) : {};
  const docker = runsDocker(draft, schedule.docker_actions);

  const save = useMutation({
    mutationFn: () => request("put", "/api/server/storage/schedule", { body: policyBody(draft, schedule.docker_actions) }),
    onSuccess: (saved) => {
      queryClient.setQueryData(serverKeys.cleanupSchedule, saved);
      void queryClient.invalidateQueries({ queryKey: serverKeys.cleanupSchedule });
      onOpenChange(false);
    },
  });

  const fail = (problem: PolicyProblem, message: string): string | undefined => (problems[problem] === true ? message : undefined);
  const toggle = (action: string, on: boolean): void =>
    setDraft((previous) => ({ ...previous, actions: on ? [...previous.actions, action] : previous.actions.filter((item) => item !== action) }));

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setTried(true);
    if (Object.keys(policyProblems(draft)).length > 0) return;
    if (docker && !draft.allowDocker) return;
    save.mutate();
  };
  const formId = "cleanup-policy-form";
  // Listed in the order they run: the cheap and safe ones first.
  const actions = schedule.schedulable;

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("server.storage.schedule.editTitle")}
      description={t("server.storage.schedule.editDescription")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("server.storage.schedule.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("server.storage.schedule.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-6">
        <Checkbox checked={draft.enabled} onCheckedChange={(enabled) => setDraft((previous) => ({ ...previous, enabled }))} label={t("server.storage.schedule.enabled")} description={t("server.storage.schedule.enabledHelp")} />
        <ScheduleFields
          value={draft.schedule}
          onChange={(next) => setDraft((previous) => ({ ...previous, schedule: next }))}
          scheduleError={fail("schedule", t("databases.policy.customRequired"))}
          timeError={fail("time", t("databases.policy.timeInvalid"))}
        />
        <Field label={t("server.storage.schedule.threshold")} description={t("server.storage.schedule.thresholdHelp")} error={fail("threshold", t("server.storage.schedule.thresholdInvalid"))}>
          <Input
            mono
            inputMode="numeric"
            value={draft.threshold}
            onValueChange={(threshold: string) => setDraft((previous) => ({ ...previous, threshold }))}
            suffix={<span className="px-2 text-12 text-fg-faint">%</span>}
            className="w-32"
            autoComplete="off"
          />
        </Field>
        <fieldset className="flex min-w-0 flex-col gap-4">
          <legend className="mb-3 text-13 font-medium text-fg">{t("server.storage.schedule.actionsLegend")}</legend>
          {actions.map((action) => (
            <Checkbox key={action} checked={draft.actions.includes(action)} onCheckedChange={(on) => toggle(action, on)} label={cleanupActionLabel(t, action)} description={cleanupActionHelp(t, action)} />
          ))}
          {problems.actions === true ? <p className="text-12 text-fail">{t("server.storage.schedule.actionsInvalid")}</p> : null}
          <p className="text-12 text-fg-faint">{t("server.storage.schedule.neverScheduled")}</p>
        </fieldset>
        {draft.actions.includes("journal") ? (
          <Field
            label={t("server.storage.schedule.journalSize")}
            optional
            description={t("server.storage.schedule.journalSizeHelp", { min: JOURNAL_MIN_MB, max: JOURNAL_MAX_MB })}
            error={fail("journal", t("server.storage.schedule.journalSizeInvalid", { min: JOURNAL_MIN_MB, max: JOURNAL_MAX_MB }))}
          >
            <Input mono inputMode="numeric" value={draft.journal} onValueChange={(journal: string) => setDraft((previous) => ({ ...previous, journal }))} suffix={<span className="px-2 text-12 text-fg-faint">MB</span>} className="w-40" autoComplete="off" />
          </Field>
        ) : null}
        {docker ? (
          <Notice tone="warning" title={t("server.storage.schedule.dockerTitle")}>
            <div className="flex flex-col gap-3">
              <p>{t("server.storage.schedule.dockerBody")}</p>
              <Checkbox checked={draft.allowDocker} onCheckedChange={(allowDocker) => setDraft((previous) => ({ ...previous, allowDocker }))} label={t("server.storage.schedule.dockerAccept")} />
              {tried && !draft.allowDocker ? <p className="text-12 text-fail">{t("server.storage.schedule.dockerRequired")}</p> : null}
            </div>
          </Notice>
        ) : null}
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("server.storage.schedule.saveFailed")} /> : null}
      </form>
    </Drawer>
  );
}

/** Automatic cleanup: the policy, its timer, Edit and Run now. */
export function CleanupScheduleCard() {
  const t = useT();
  const jobs = useServerJob();
  const query = useQuery(cleanupScheduleQuery());
  const [editing, setEditing] = useState(false);
  const [running, setRunning] = useState(false);

  if (query.isError && query.data === undefined) {
    return (
      <Card level={2} title={t("server.storage.schedule.title")} padding="sm">
        <ServerErrorBlock compact error={query.error} title={t("server.storage.schedule.loadFailed")} onRetry={() => void query.refetch()} />
      </Card>
    );
  }
  if (query.data === undefined) {
    return (
      <Card loading level={2} title={t("server.storage.schedule.title")} padding="sm" actions={<Skeleton className="h-control-sm w-24 rounded-control" />}>
        <Skeleton className="h-32 w-full" />
      </Card>
    );
  }
  const schedule = query.data;
  const policy = schedule.policy;
  const on = policy.enabled && schedule.timer.installed;
  const items: KeyValueItem[] = [
    {
      label: t("server.storage.schedule.state"),
      value: on ? <Badge tone="ok">{t("server.storage.schedule.on")}</Badge> : <Badge>{t("server.storage.schedule.off")}</Badge>,
      mono: false,
      copy: false,
    },
    ...(policy.enabled
      ? [
          { label: t("server.storage.schedule.when"), value: policyWhen(t, policy), mono: false, copy: false } satisfies KeyValueItem,
          {
            label: t("server.storage.schedule.runsWhen"),
            value: policy.threshold_percent === 0 ? t("server.storage.schedule.runsAlways") : t("server.storage.schedule.runsAbove", { percent: policy.threshold_percent }),
            mono: false,
            copy: false,
          } satisfies KeyValueItem,
          { label: t("server.storage.schedule.cleans"), value: (policy.actions ?? []).map((action) => cleanupActionLabel(t, action)).join(", "), mono: false, copy: false } satisfies KeyValueItem,
          {
            label: t("server.storage.schedule.next"),
            value: schedule.timer.next_run ? <RelativeTime value={schedule.timer.next_run} /> : null,
            mono: false,
            copy: false,
          } satisfies KeyValueItem,
          {
            label: t("server.storage.schedule.last"),
            value: schedule.timer.last_run ? <RelativeTime value={schedule.timer.last_run} /> : null,
            mono: false,
            copy: false,
          } satisfies KeyValueItem,
        ]
      : []),
  ];
  return (
    <Card
      level={2}
      title={t("server.storage.schedule.title")}
      description={t("server.storage.schedule.description")}
      padding="sm"
      actions={
        <div className="flex items-center gap-2">
          {policy.enabled ? (
            <Button size="sm" variant="ghost" disabled={jobs.busy} onClick={() => setRunning(true)}>
              {t("server.storage.schedule.runNow")}
            </Button>
          ) : null}
          <Button size="sm" onClick={() => setEditing(true)}>
            {policy.enabled ? t("server.storage.schedule.edit") : t("server.storage.schedule.set")}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <KeyValueList items={items} />
        {!policy.enabled ? <p className="text-13 text-fg-muted">{t("server.storage.schedule.offNote")}</p> : null}
        {policy.enabled && !schedule.timer.installed ? (
          <Notice tone="warning" title={t("server.storage.schedule.noTimerTitle")}>
            {t("server.storage.schedule.noTimerBody")}
          </Notice>
        ) : null}
      </div>
      {/* Re-mounted on each open so the form starts from what is stored now. */}
      {editing ? <PolicyEditor schedule={schedule} open onOpenChange={setEditing} /> : null}
      {running ? (
        <ActionDialog
          title={t("server.storage.schedule.runTitle")}
          description={t("server.storage.schedule.runDescription")}
          actionLabel={t("server.storage.schedule.runAction")}
          destructive={policy.allow_docker && (policy.actions ?? []).some((action) => schedule.docker_actions.includes(action))}
          onClose={() => setRunning(false)}
          onConfirm={async () => {
            const accepted = await request("post", "/api/server/storage/schedule/run");
            jobs.track(accepted.job_id, "cleanup");
          }}
        >
          <ul aria-label={t("server.storage.schedule.runListLabel")} className="flex flex-col gap-0.5 text-13">
            {(policy.actions ?? []).map((action) => (
              <li key={action}>{cleanupActionLabel(t, action)}</li>
            ))}
          </ul>
        </ActionDialog>
      ) : null}
    </Card>
  );
}
