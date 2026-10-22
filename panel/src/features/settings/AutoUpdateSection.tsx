/**
 * Settings > About > Automatic updates (checklist N-28): whether this server installs Noust's new
 * releases by itself, inside which hours, on which days, and for which releases; the next window
 * and what the last run did. Saving writes or removes a systemd timer, in sudo mode, and the
 * server judges every value again. The update itself is "Update Noust" with a narrower door: the
 * same preflight, the same transient unit.
 */

import { Settings2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { autoUpdateKeys, autoUpdateQuery, saveAutoUpdate } from "../../api/queries/autoUpdate";
import type { AutoUpdate } from "../../api/queries/autoUpdate";
import { announce } from "../../app/Announcer";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Drawer } from "../../components/ui/Drawer";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { WEEKDAYS, weekdayLabel } from "../databases/backups/schedule";
import type { Weekday } from "../databases/backups/schedule";
import { CHANNELS, EMPTY_DRAFT, bodyOf, crossesMidnight, daysWords, draftOf, problemsOf } from "./autoUpdate";
import type { AutoUpdateDraft, Channel } from "./autoUpdate";

function channelOptions(t: T) {
  return CHANNELS.map((value) => ({
    value,
    label: t(`autoUpdate.channel.${value}`),
    description: t(`autoUpdate.channel.${value}Description`),
  }));
}

function Editor({ state, onClose }: { state: AutoUpdate; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  // A server that has never set it up starts from the defaults, switched on.
  const configured = state.settings.enabled || state.timer.installed;
  const [draft, setDraft] = useState<AutoUpdateDraft>(() => (configured ? draftOf(state.settings) : EMPTY_DRAFT));
  const [tried, setTried] = useState(false);
  const problems = tried ? problemsOf(draft) : {};

  const save = useMutation({
    mutationFn: () => saveAutoUpdate(bodyOf(draft)),
    onSuccess: (saved) => {
      queryClient.setQueryData(autoUpdateKeys.all, saved);
      void queryClient.invalidateQueries({ queryKey: autoUpdateKeys.all });
      announce(t("autoUpdate.editor.saved"));
      toast.success(t("autoUpdate.editor.saved"));
      onClose();
    },
  });

  const toggleDay = (day: Weekday, on: boolean): void =>
    setDraft((previous) => ({ ...previous, days: on ? [...previous.days, day] : previous.days.filter((item) => item !== day) }));
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setTried(true);
    if (Object.keys(problemsOf(draft)).length > 0) return;
    save.mutate();
  };
  const formId = "auto-update-form";
  const timeError = (which: "from" | "to"): string | undefined => (problems[which] === true ? t("autoUpdate.editor.timeInvalid") : problems.same === true && which === "to" ? t("autoUpdate.editor.sameTime") : undefined);

  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t("autoUpdate.editor.title")}
      description={t("autoUpdate.editor.description")}
      footer={
        <>
          <Button onClick={onClose}>{t("common.words.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("common.words.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-6">
        <Checkbox
          checked={draft.enabled}
          onCheckedChange={(enabled) => setDraft((previous) => ({ ...previous, enabled }))}
          label={t("autoUpdate.editor.enabled")}
          description={t("autoUpdate.editor.enabledHelp")}
        />
        <ChoiceCards<Channel>
          legend={t("autoUpdate.channel.label")}
          options={channelOptions(t)}
          value={draft.channel}
          onValueChange={(channel) => setDraft((previous) => ({ ...previous, channel }))}
        />
        <fieldset className="flex min-w-0 flex-col gap-4">
          <legend className="mb-3 text-13 font-medium text-fg">{t("autoUpdate.editor.hours")}</legend>
          <div className="flex flex-wrap gap-4">
            <Field label={t("autoUpdate.editor.from")} error={timeError("from")}>
              <Input mono value={draft.from} onValueChange={(from: string) => setDraft((previous) => ({ ...previous, from }))} placeholder="03:00" className="w-32" autoComplete="off" />
            </Field>
            <Field label={t("autoUpdate.editor.to")} error={timeError("to")}>
              <Input mono value={draft.to} onValueChange={(to: string) => setDraft((previous) => ({ ...previous, to }))} placeholder="05:00" className="w-32" autoComplete="off" />
            </Field>
          </div>
          <p className="text-12 text-fg-muted">
            {t("autoUpdate.editor.hoursHelp")} {crossesMidnight(draft.from, draft.to) ? t("autoUpdate.editor.crossesMidnight") : null}
          </p>
        </fieldset>
        <fieldset className="flex min-w-0 flex-col gap-3">
          <legend className="mb-3 text-13 font-medium text-fg">{t("autoUpdate.editor.days")}</legend>
          <Checkbox checked={draft.everyDay} onCheckedChange={(everyDay) => setDraft((previous) => ({ ...previous, everyDay }))} label={t("autoUpdate.editor.everyDay")} />
          {!draft.everyDay ? (
            <div role="group" aria-label={t("autoUpdate.editor.chooseDays")} className="flex flex-wrap gap-x-5 gap-y-2">
              {WEEKDAYS.map((day) => (
                <Checkbox key={day} checked={draft.days.includes(day)} onCheckedChange={(on) => toggleDay(day, on)} label={weekdayLabel(day, t.locale)} />
              ))}
            </div>
          ) : null}
          {problems.days === true ? <p className="text-12 text-fail">{t("autoUpdate.editor.daysRequired")}</p> : null}
        </fieldset>
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("autoUpdate.editor.saveFailed")} /> : null}
      </form>
    </Drawer>
  );
}

/** What the last run did, as one line: the outcome, and the server's own sentence. */
function lastRunItem(t: T, last: NonNullable<AutoUpdate["last_run"]>): KeyValueItem {
  const outcome = last.outcome === "updated" ? t("autoUpdate.outcome.updated") : last.outcome === "failed" ? t("common.statusPill.failed") : t("autoUpdate.outcome.skipped");
  return {
    label: t("autoUpdate.facts.lastRun"),
    value: (
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-13 text-fg">
          {outcome} <RelativeTime value={last.at} />
        </span>
        {/* The server's own words, as it said them: what it decided and why. */}
        <span className="text-12 text-pretty text-fg-muted">{last.outcome === "updated" && last.to_version ? t("autoUpdate.outcome.updatedTo", { from: last.from_version, to: last.to_version }) : last.detail}</span>
      </span>
    ),
    mono: false,
    copy: false,
  };
}

/** On and working, on and not doing anything, or off: a pill of the card's header says it. */
function stateOf(state: AutoUpdate): "on" | "problem" | "off" {
  const { settings } = state;
  if (!settings.enabled) return "off";
  return state.supported && state.check_enabled && state.timer.installed ? "on" : "problem";
}

const PILL: Record<"on" | "problem" | "off", Status> = { on: "running", problem: "warning", off: "stopped" };

/** The Automatic updates card of Settings > About: its state is a pill in its header. */
export function AutoUpdateSection() {
  const t = useT();
  const query = useQuery(autoUpdateQuery());
  return (
    <QueryState
      query={query}
      label={t("autoUpdate.what")}
      skeleton={
        <Card loading level={3} title={t("autoUpdate.title")} description={t("autoUpdate.description")}>
          <div className="flex flex-col gap-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        </Card>
      }
    >
      {(state) => <AutoUpdateCard state={state} />}
    </QueryState>
  );
}

function AutoUpdateCard({ state }: { state: AutoUpdate }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const { settings } = state;
  const featureState = stateOf(state);
  const acrossMidnight = crossesMidnight(...(settings.window.split("-") as [string, string]));
  const days = daysWords(settings.days, t.locale, t("autoUpdate.facts.everyDay"));

  const items: KeyValueItem[] = settings.enabled
    ? [
        { label: t("autoUpdate.facts.releases"), value: t(`autoUpdate.channel.${settings.channel as Channel}`), mono: false, copy: false },
        {
          label: t("autoUpdate.facts.window"),
          value: <Mono>{acrossMidnight ? t("autoUpdate.facts.windowAcrossMidnight", { window: settings.window }) : settings.window}</Mono>,
          mono: false,
          copy: false,
        },
        { label: t("autoUpdate.facts.days"), value: days, mono: false, copy: false },
        {
          label: t("autoUpdate.facts.nextWindow"),
          value: state.window_open ? t("autoUpdate.facts.openNow") : state.next_window ? <RelativeTime value={state.next_window} /> : null,
          mono: false,
          copy: false,
        },
        {
          label: t("autoUpdate.facts.timer"),
          value: state.timer.installed ? t("autoUpdate.facts.timerInstalled") : t("autoUpdate.facts.timerMissing"),
          mono: false,
          copy: false,
        },
      ]
    : [];
  if (state.last_run) items.push(lastRunItem(t, state.last_run));

  return (
    <>
      <Card
        level={3}
        title={t("autoUpdate.title")}
        status={
          <StatusPill
            state={PILL[featureState]}
            label={featureState === "off" ? t("autoUpdate.pill.off") : featureState === "problem" ? t("autoUpdate.pill.problem") : t("autoUpdate.pill.on")}
            size="sm"
          />
        }
        description={
          !settings.enabled
            ? t("autoUpdate.offBody")
            : !state.supported
              ? `${state.reason ?? t("autoUpdate.notSupported")} ${state.hint ?? ""}`.trim()
              : !state.check_enabled
                ? t("autoUpdate.checkOffBody")
                : t("autoUpdate.onBody", { channel: t(`autoUpdate.channelWords.${settings.channel as Channel}`) })
        }
        actions={
          <Button icon={<Settings2 aria-hidden="true" />} size="sm" onClick={() => setEditing(true)}>
            {settings.enabled || state.timer.installed ? t("common.words.edit") : t("autoUpdate.set")}
          </Button>
        }
      >
        {items.length > 0 ? <KeyValueList items={items} /> : undefined}
      </Card>
      {/* Re-mounted on each open so the form starts from what is stored now. */}
      {editing ? <Editor state={state} onClose={() => setEditing(false)} /> : null}
    </>
  );
}
