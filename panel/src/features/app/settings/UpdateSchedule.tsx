/**
 * When the app updates itself (`noust deploy schedule set|show|remove`,
 * `GET|PUT|DELETE /api/apps/{domain}/update-schedule`). A run asks whether there is anything new
 * and does nothing if not, takes the backup an update needs and refuses to update without it,
 * is held by a pause or a freeze of deploys, and passes the health gate every update passes.
 * How the last run ended is kept with the schedule and said here.
 *
 * A row of Automation: what it is and how the last run ended, with a button that opens the
 * schedule in a drawer. The drawer is a form with its own footer, so the row never shares a surface
 * with a Save button.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { updateScheduleKey, updateScheduleQuery } from "../../../api/queries/rollout";
import type { UpdateSchedule } from "../../../api/queries/rollout";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { FieldGroup } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { Setting } from "../../../components/ui/Setting";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { splitErrors } from "../../settings/formErrors";
import { olderServer } from "./queries";
import { RowError, RowLoading } from "./settingRows";
import { SETTINGS_ICONS } from "./objectIcons";

type When = "daily" | "weekly" | "monthly" | "custom";

interface Draft {
  when: When;
  custom: string;
  enabled: boolean;
  backupFirst: boolean;
  onlyIfNew: boolean;
}

const FRESH: Draft = { when: "weekly", custom: "", enabled: true, backupFirst: true, onlyIfNew: true };

/** The form for a schedule that exists: its calendar is one of the shorthands when it is what they stand for. */
export function draftOf(schedule: UpdateSchedule): Draft {
  if (!schedule.scheduled || typeof schedule.calendar !== "string") return FRESH;
  const found = (["daily", "weekly", "monthly"] as const).find((name) => schedule.aliases?.[name] === schedule.calendar);
  return {
    when: found ?? "custom",
    custom: found === undefined ? schedule.calendar : "",
    enabled: schedule.enabled,
    backupFirst: schedule.backup_first,
    onlyIfNew: schedule.only_if_new,
  };
}

function sameDraft(a: Draft, b: Draft): boolean {
  return a.when === b.when && a.custom.trim() === b.custom.trim() && a.enabled === b.enabled && a.backupFirst === b.backupFirst && a.onlyIfNew === b.onlyIfNew;
}

/** The outcome of the last run in words. An outcome the console does not know is shown as it came. */
function outcomeWords(t: T, outcome: string): string {
  switch (outcome) {
    case "updated":
      return t("rollout.schedule.outcomeUpdated");
    case "nothing_new":
      return t("rollout.schedule.outcomeNothingNew");
    case "held":
      return t("rollout.schedule.outcomeHeld");
    case "backup_failed":
      return t("rollout.schedule.outcomeBackupFailed");
    case "failed":
      return t("rollout.schedule.outcomeFailed");
    default:
      return outcome;
  }
}

interface DrawerProps {
  domain: string;
  data: UpdateSchedule;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRemove: () => void;
}

/** The schedule as a form in a drawer: when, how careful each run is, and whether it runs at all. */
function ScheduleDrawer({ domain, data, open, onOpenChange, onRemove }: DrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const stored = draftOf(data);
  const [draft, setDraft] = useState<Draft>(stored);
  const [submitted, setSubmitted] = useState(false);

  const calendar = draft.when === "custom" ? draft.custom.trim() : draft.when;
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/update-schedule", {
        params: { domain },
        body: { calendar, enabled: draft.enabled, backup_first: draft.backupFirst, only_if_new: draft.onlyIfNew },
      }),
    onSuccess: (result) => {
      queryClient.setQueryData<UpdateSchedule>(updateScheduleKey(domain), result);
      setSubmitted(false);
      onOpenChange(false);
      announce(result.enabled ? t("rollout.schedule.saved", { domain, calendar: result.calendar ?? "" }) : t("rollout.schedule.savedOff", { domain }));
    },
  });

  const split = splitErrors(save.error, ["calendar"] as const, "calendar");
  const customMissing = submitted && draft.when === "custom" && draft.custom.trim() === "" ? t("rollout.schedule.customRequired") : undefined;
  const calendarError = customMissing ?? split.fields.calendar;
  const changed = !data.scheduled || !sameDraft(draft, stored);

  const change = (next: Partial<Draft>): void => {
    setDraft((previous) => ({ ...previous, ...next }));
    if (save.isError) save.reset();
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (draft.when === "custom" && draft.custom.trim() === "") return;
    save.mutate();
  };

  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next && save.isPending) return;
        onOpenChange(next);
      }}
      title={t("rollout.schedule.drawerTitle", { domain })}
      description={t("rollout.schedule.description")}
      footer={
        <>
          {data.scheduled ? (
            <Button variant="ghost" className="mr-auto" disabled={save.isPending} onClick={onRemove}>
              {t("rollout.schedule.remove")}
            </Button>
          ) : null}
          <Button disabled={save.isPending} onClick={() => onOpenChange(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={!changed}>
            {t("rollout.schedule.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate className="flex flex-col gap-5" onSubmit={submit}>
        <Field label={t("rollout.schedule.whenLabel")} nativeLabel={false}>
          <Select
            options={[
              { value: "daily", label: t("rollout.schedule.whenDaily") },
              { value: "weekly", label: t("rollout.schedule.whenWeekly") },
              { value: "monthly", label: t("rollout.schedule.whenMonthly") },
              { value: "custom", label: t("rollout.schedule.whenCustom") },
            ]}
            value={draft.when}
            onValueChange={(when) => change({ when })}
          />
        </Field>
        {draft.when === "custom" ? (
          <Field label={t("rollout.schedule.customLabel")} description={t("rollout.schedule.customDescription")} error={calendarError}>
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder="Sun *-*-* 03:00"
              value={draft.custom}
              onValueChange={(custom: string) => change({ custom })}
            />
          </Field>
        ) : null}
        <FieldGroup legend={t("rollout.schedule.careLegend")}>
          <Checkbox
            label={t("rollout.schedule.backupLabel")}
            description={t("rollout.schedule.backupDescription")}
            checked={draft.backupFirst}
            onCheckedChange={(backupFirst) => change({ backupFirst })}
          />
          <Checkbox
            label={t("rollout.schedule.onlyIfNewLabel")}
            description={t("rollout.schedule.onlyIfNewDescription")}
            checked={draft.onlyIfNew}
            onCheckedChange={(onlyIfNew) => change({ onlyIfNew })}
          />
          <Checkbox label={t("rollout.schedule.enabledLabel")} checked={draft.enabled} onCheckedChange={(enabled) => change({ enabled })} />
        </FieldGroup>
        {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("rollout.schedule.saveFailed", { domain })} /> : null}
      </form>
    </Drawer>
  );
}

/** The row: whether the app updates itself, when it runs next, how the last run ended, and the drawer that sets it. */
export function UpdateScheduleSetting({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery({ ...updateScheduleQuery(domain), retry: false });
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);

  if (query.isPending) return <RowLoading label={t("rollout.schedule.loading")} />;
  // A server older than 3.4 has no scheduled updates: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) return <RowError title={t("rollout.schedule.readFailed")} error={query.error} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;

  const data = query.data;
  const lastRun = data.last_run_at;
  return (
    <>
      <Setting
        label={t("rollout.schedule.title")}
        help={t("rollout.schedule.help")}
        {...(data.scheduled ? { status: <StatusPill state={data.enabled ? "running" : "stopped"} size="sm" label={data.enabled ? t("rollout.schedule.pillOn") : t("rollout.schedule.pillOff")} /> } : {})}
        action={
          <Button icon={<SETTINGS_ICONS.schedule aria-hidden="true" />} size="sm" onClick={() => setEditing(true)}>
            {data.scheduled ? t("rollout.schedule.edit") : t("rollout.schedule.set")}
          </Button>
        }
      >
        {data.scheduled ? (
          <p className="flex flex-col gap-0.5 text-12 text-fg-muted">
            <span>
              {data.next_run !== null && data.next_run !== undefined
                ? t.rich("rollout.schedule.nextRun", { time: <RelativeTime value={data.next_run} /> })
                : t("rollout.schedule.noNextRun")}
            </span>
            <span data-testid="schedule-last-run">
              {lastRun !== null && lastRun !== undefined
                ? t.rich("rollout.schedule.lastRun", { time: <RelativeTime value={lastRun} />, outcome: outcomeWords(t, data.last_outcome ?? "") })
                : t("rollout.schedule.neverRan")}
            </span>
            {data.last_detail !== null && data.last_detail !== undefined && data.last_detail !== "" ? <Mono className="text-12 break-words">{data.last_detail}</Mono> : null}
          </p>
        ) : null}
      </Setting>
      {editing ? (
        <ScheduleDrawer
          domain={domain}
          data={data}
          open
          onOpenChange={setEditing}
          onRemove={() => {
            setEditing(false);
            setRemoving(true);
          }}
        />
      ) : null}
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        server={node}
        title={t("rollout.schedule.removeTitle", { domain })}
        description={t("rollout.schedule.removeDescription")}
        actionLabel={t("rollout.schedule.removeAction")}
        onConfirm={async () => {
          try {
            const result = await request("delete", "/api/apps/{domain}/update-schedule", { params: { domain } });
            queryClient.setQueryData<UpdateSchedule>(updateScheduleKey(domain), result);
            announce(t("rollout.schedule.removed", { domain }));
          } catch (error) {
            reportActionError(t("rollout.schedule.removeFailed", { domain }), error);
          }
        }}
      />
    </>
  );
}
