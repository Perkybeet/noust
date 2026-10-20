/**
 * How much of an app's deploy history is kept and how its unit is told to stop (`noust deploy
 * keep`, `noust app stop-grace`, `PUT /api/apps/{domain}/rollout`). A row of Automation with a
 * drawer that edits them and has its own footer, sending only the fields that changed, because a
 * stop policy rewrites the unit (and restarts nothing).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { rolloutKey, rolloutQuery } from "../../../api/queries/rollout";
import type { Rollout } from "../../../api/queries/rollout";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import type { SelectOption } from "../../../components/ui/Select";
import { Setting } from "../../../components/ui/Setting";
import { useT } from "../../../i18n";
import { splitErrors } from "../../settings/formErrors";
import { olderServer } from "./queries";
import { RowError, RowLoading } from "./settingRows";
import {
  DEFAULT_SIGNAL,
  HISTORY_MAX,
  HISTORY_MIN,
  ROLLOUT_FIELDS,
  STOP_SIGNALS,
  STOP_TIMEOUT_MAX,
  STOP_TIMEOUT_MIN,
  parseRolloutDraft,
  rolloutChanges,
  rolloutDraftChanges,
  rolloutDraftOf,
} from "./rolloutLimits";
import type { RolloutDraft, RolloutField } from "./rolloutLimits";

interface DrawerProps {
  domain: string;
  data: Rollout;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The delivery settings as a form in a drawer, mounted fresh for every opening. */
function LimitsDrawer({ domain, data, open, onOpenChange }: DrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const stored = rolloutDraftOf(data);
  const [draft, setDraft] = useState<RolloutDraft>(stored);
  const [submitted, setSubmitted] = useState(false);

  const parsed = parseRolloutDraft(draft, t.locale);
  const save = useMutation({
    mutationFn: () => {
      if (parsed.values === null) throw new Error("The form has errors");
      const current = parseRolloutDraft(stored, t.locale).values;
      if (current === null) throw new Error("The stored settings do not parse");
      return request("put", "/api/apps/{domain}/rollout", { params: { domain }, body: rolloutChanges(parsed.values, current) });
    },
    onSuccess: (result) => {
      queryClient.setQueryData<Rollout>(rolloutKey(domain), result);
      announce(t("rollout.limits.saved", { domain }));
      onOpenChange(false);
    },
  });

  const split = splitErrors(save.error, ROLLOUT_FIELDS);
  const errors: Partial<Record<RolloutField, string>> = { ...(submitted ? parsed.errors : {}), ...split.fields };
  const changes = rolloutDraftChanges(draft, stored);
  const edit = (field: RolloutField, value: string): void => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    if (save.isError) save.reset();
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (parsed.values === null || save.isPending) return;
    save.mutate();
  };
  const signalOptions: readonly SelectOption[] = [
    { value: DEFAULT_SIGNAL, label: t("rollout.limits.stopSignalDefault") },
    ...STOP_SIGNALS.map((signal) => ({ value: signal, label: signal })),
  ];

  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next && save.isPending) return;
        onOpenChange(next);
      }}
      title={t("rollout.limits.title")}
      description={t("rollout.limits.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => onOpenChange(false)}>
            {t("appSettings.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={changes === 0}>
            {t("rollout.limits.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate className="flex flex-col gap-5" onSubmit={submit}>
        <Field
          label={t("rollout.limits.historyLabel")}
          description={t("rollout.limits.historyDescription", { min: HISTORY_MIN, max: HISTORY_MAX, server: data.history_keep_server })}
          error={errors.history_keep}
        >
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            placeholder={t("rollout.limits.historyPlaceholder")}
            value={draft.history_keep}
            onValueChange={(value: string) => edit("history_keep", value)}
            className="w-48"
          />
        </Field>
        {data.stop_supported ? (
          <>
            <Field
              label={t("rollout.limits.stopTimeoutLabel")}
              description={t("rollout.limits.stopTimeoutDescription", { min: STOP_TIMEOUT_MIN, max: STOP_TIMEOUT_MAX })}
              error={errors.stop_timeout}
            >
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                placeholder={t("rollout.limits.stopTimeoutPlaceholder")}
                suffix={t("rollout.limits.stopTimeoutSuffix")}
                value={draft.stop_timeout}
                onValueChange={(value: string) => edit("stop_timeout", value)}
                className="w-48"
              />
            </Field>
            <Field label={t("rollout.limits.stopSignalLabel")} nativeLabel={false} error={errors.stop_signal}>
              <Select mono options={signalOptions} value={draft.stop_signal} onValueChange={(value) => edit("stop_signal", value)} />
            </Field>
            <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("rollout.limits.stopApplied")}</p>
          </>
        ) : (
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("rollout.limits.stopUnsupported")}</p>
        )}
        {save.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("rollout.limits.saveFailed")} /> : null}
      </form>
    </Drawer>
  );
}

/** The row: how many deploys are kept and the time and signal a unit stops with, and the drawer that sets them. */
export function RolloutLimitsSetting({ domain }: { domain: string }) {
  const t = useT();
  const query = useQuery({ ...rolloutQuery(domain), retry: false });
  const [editing, setEditing] = useState(false);

  if (query.isPending) return <RowLoading label={t("rollout.limits.loading")} />;
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) return <RowError title={t("rollout.limits.readFailed")} error={query.error} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;

  const data = query.data;
  return (
    <>
      <Setting
        label={t("rollout.limits.title")}
        help={t("rollout.limits.help")}
        action={
          <Button size="sm" onClick={() => setEditing(true)}>
            {t("rollout.limits.edit")}
          </Button>
        }
      >
        <p className="text-12 text-fg-muted" data-testid="delivery-summary">
          {data.history_keep === null ? t("rollout.limits.summaryServer", { count: data.history_keep_server }) : t("rollout.limits.summaryOwn", { count: data.history_keep })}
        </p>
      </Setting>
      {editing ? <LimitsDrawer domain={domain} data={data} open onOpenChange={setEditing} /> : null}
    </>
  );
}
