/**
 * When the app updates itself (`noust deploy schedule set|show|remove`,
 * `GET|PUT|DELETE /api/apps/{domain}/update-schedule`). A run asks whether there is anything new
 * and does nothing if not, takes the backup an update needs and refuses to update without it,
 * is held by a pause or a freeze of deploys, and passes the health gate every update passes.
 * How the last run ended is kept with the schedule and said here.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { updateScheduleKey, updateScheduleQuery } from "../../../api/queries/rollout";
import type { UpdateSchedule } from "../../../api/queries/rollout";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { splitErrors } from "../../settings/formErrors";
import { olderServer } from "./queries";

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

/** The card: whether the app updates itself, when, how careful each run is, and how the last one ended. */
export function UpdateScheduleCard({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery({ ...updateScheduleQuery(domain), retry: false });
  const stored = query.data !== undefined ? draftOf(query.data) : FRESH;
  const [baseline, setBaseline] = useState<Draft>(FRESH);
  const [draft, setDraft] = useState<Draft>(FRESH);
  const [editing, setEditing] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [removing, setRemoving] = useState(false);

  // The schedule changed underneath an untouched form (saved here, or from a terminal): follow it.
  if (query.data !== undefined && !sameDraft(stored, baseline)) {
    setBaseline(stored);
    if (sameDraft(draft, baseline)) setDraft(stored);
  }

  const calendar = draft.when === "custom" ? draft.custom.trim() : draft.when;
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/update-schedule", {
        params: { domain },
        body: { calendar, enabled: draft.enabled, backup_first: draft.backupFirst, only_if_new: draft.onlyIfNew },
      }),
    onSuccess: (result) => {
      queryClient.setQueryData<UpdateSchedule>(updateScheduleKey(domain), result);
      setBaseline(draftOf(result));
      setDraft(draftOf(result));
      setSubmitted(false);
      setEditing(false);
      announce(result.enabled ? t("rollout.schedule.saved", { domain, calendar: result.calendar ?? "" }) : t("rollout.schedule.savedOff", { domain }));
    },
  });
  const title = t("rollout.schedule.title");

  if (query.isPending) {
    return (
      <Card title={title}>
        <div aria-busy="true">
          <span className="sr-only">{t("rollout.schedule.loading")}</span>
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      </Card>
    );
  }
  // A server older than 3.4 has no scheduled updates: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={query.error} title={t("rollout.schedule.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      </Card>
    );
  }
  const data = query.data;
  const showForm = data.scheduled || editing;
  const split = splitErrors(save.error, ["calendar"] as const, "calendar");
  const customMissing = submitted && draft.when === "custom" && draft.custom.trim() === "" ? t("rollout.schedule.customRequired") : undefined;
  const calendarError = customMissing ?? split.fields.calendar;
  const changed = !data.scheduled || !sameDraft(draft, stored);

  const submit = (): void => {
    setSubmitted(true);
    if (draft.when === "custom" && draft.custom.trim() === "") return;
    save.mutate();
  };

  const lastRun = data.last_run_at;
  return (
    <Card title={title} description={t("rollout.schedule.description")}>
      <div className="flex flex-col gap-4">
        {data.scheduled ? (
          <FeatureState state={data.enabled ? "on" : "off"} title={data.enabled ? t("rollout.schedule.on") : t("rollout.schedule.paused")}>
            <span className="flex flex-col gap-0.5">
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
            </span>
          </FeatureState>
        ) : (
          <FeatureState
            state="off"
            title={t("rollout.schedule.off")}
            action={
              !editing ? (
                <Button size="sm" onClick={() => setEditing(true)}>
                  {t("rollout.schedule.turnOn")}
                </Button>
              ) : undefined
            }
          >
            {t("rollout.schedule.offBody")}
          </FeatureState>
        )}
        {showForm ? (
          <form
            noValidate
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("rollout.schedule.whenLabel")} nativeLabel={false}>
                <Select
                  options={[
                    { value: "daily", label: t("rollout.schedule.whenDaily") },
                    { value: "weekly", label: t("rollout.schedule.whenWeekly") },
                    { value: "monthly", label: t("rollout.schedule.whenMonthly") },
                    { value: "custom", label: t("rollout.schedule.whenCustom") },
                  ]}
                  value={draft.when}
                  onValueChange={(when) => {
                    setDraft((previous) => ({ ...previous, when }));
                    if (save.isError) save.reset();
                  }}
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
                    onValueChange={(custom: string) => {
                      setDraft((previous) => ({ ...previous, custom }));
                      if (save.isError) save.reset();
                    }}
                  />
                </Field>
              ) : null}
            </div>
            <div className="flex flex-col gap-3">
              <Switch
                label={t("rollout.schedule.backupLabel")}
                description={t("rollout.schedule.backupDescription")}
                checked={draft.backupFirst}
                onCheckedChange={(backupFirst) => setDraft((previous) => ({ ...previous, backupFirst }))}
              />
              <Switch
                label={t("rollout.schedule.onlyIfNewLabel")}
                description={t("rollout.schedule.onlyIfNewDescription")}
                checked={draft.onlyIfNew}
                onCheckedChange={(onlyIfNew) => setDraft((previous) => ({ ...previous, onlyIfNew }))}
              />
              <Switch label={t("rollout.schedule.enabledLabel")} checked={draft.enabled} onCheckedChange={(enabled) => setDraft((previous) => ({ ...previous, enabled }))} />
            </div>
            {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("rollout.schedule.saveFailed", { domain })} /> : null}
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" variant="primary" size="sm" loading={save.isPending} disabled={!changed}>
                {t("rollout.schedule.save")}
              </Button>
              {data.scheduled ? (
                <Button size="sm" variant="ghost" onClick={() => setRemoving(true)}>
                  {t("rollout.schedule.remove")}
                </Button>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                  {t("appSettings.cancel")}
                </Button>
              )}
            </div>
          </form>
        ) : null}
      </div>
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
            setBaseline(FRESH);
            setDraft(FRESH);
            setEditing(false);
            announce(t("rollout.schedule.removed", { domain }));
          } catch (error) {
            reportActionError(t("rollout.schedule.removeFailed", { domain }), error);
          }
        }}
      />
    </Card>
  );
}
