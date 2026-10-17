/**
 * How much of an app's deploy history is kept and how its unit is told to stop (`noust deploy
 * keep`, `noust app stop-grace`, `PUT /api/apps/{domain}/rollout`). A part of the Deploys form:
 * saved from its one save bar, sending only the fields that changed, because a stop policy
 * rewrites the unit (and restarts nothing).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { rolloutKey, rolloutQuery } from "../../../api/queries/rollout";
import type { Rollout } from "../../../api/queries/rollout";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import type { SelectOption } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { splitErrors } from "../../settings/formErrors";
import type { FormPart } from "./formParts";
import { olderServer } from "./queries";
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

const EMPTY: RolloutDraft = { history_keep: "", stop_timeout: "", stop_signal: DEFAULT_SIGNAL };

export interface RolloutLimitsPart {
  /** The save bar's part; null while the settings are not read, or on a server that has none. */
  part: FormPart | null;
  query: UseQueryResult<Rollout>;
  draft: RolloutDraft;
  edit: (field: RolloutField, value: string) => void;
  errors: Partial<Record<RolloutField, string>>;
  unplaced: unknown;
}

/** The delivery settings of an app as a part of the Deploys form. */
export function useRolloutLimits(domain: string): RolloutLimitsPart {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery({ ...rolloutQuery(domain), retry: false });
  const stored = query.data !== undefined ? rolloutDraftOf(query.data) : null;
  const [baseline, setBaseline] = useState<RolloutDraft>(EMPTY);
  const [draft, setDraft] = useState<RolloutDraft>(EMPTY);
  const [submitted, setSubmitted] = useState(false);

  // The settings changed underneath an untouched form (saved here, or from a terminal): follow them.
  if (stored !== null && rolloutDraftChanges(stored, baseline) > 0) {
    setBaseline(stored);
    if (rolloutDraftChanges(draft, baseline) === 0) setDraft(stored);
  }

  const parsed = parseRolloutDraft(draft, t.locale);
  const save = useMutation({
    mutationFn: () => {
      if (parsed.values === null || stored === null) throw new Error("The form has errors");
      const current = parseRolloutDraft(stored, t.locale).values;
      if (current === null) throw new Error("The stored settings do not parse");
      return request("put", "/api/apps/{domain}/rollout", { params: { domain }, body: rolloutChanges(parsed.values, current) });
    },
    onSuccess: (result) => {
      queryClient.setQueryData<Rollout>(rolloutKey(domain), result);
      setDraft(rolloutDraftOf(result));
      setBaseline(rolloutDraftOf(result));
      setSubmitted(false);
    },
  });

  const split = splitErrors(save.error, ROLLOUT_FIELDS);
  const errors: Partial<Record<RolloutField, string>> = { ...(submitted ? parsed.errors : {}), ...split.fields };

  const part: FormPart | null =
    stored === null
      ? null
      : {
          changes: rolloutDraftChanges(draft, stored),
          elevated: false,
          check: () => {
            setSubmitted(true);
            return parsed.values !== null;
          },
          save: async () => {
            await save.mutateAsync();
          },
          discard: () => {
            setDraft(stored);
            setSubmitted(false);
            save.reset();
          },
        };

  return {
    part,
    query,
    draft,
    edit: (field, value) => {
      setDraft((previous) => ({ ...previous, [field]: value }));
      if (save.isError) save.reset();
    },
    errors,
    unplaced: save.isError ? split.form : null,
  };
}

/** The card: how many deploys are kept, and the time and signal a unit stops with. */
export function RolloutLimitsCard({ limits }: { limits: RolloutLimitsPart }) {
  const t = useT();
  const { query, draft, edit, errors } = limits;
  const title = t("rollout.limits.title");

  if (query.isPending) {
    return (
      <Card title={title}>
        <div aria-busy="true">
          <span className="sr-only">{t("rollout.limits.loading")}</span>
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      </Card>
    );
  }
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={query.error} title={t("rollout.limits.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      </Card>
    );
  }
  const data = query.data;
  const signalOptions: readonly SelectOption[] = [
    { value: DEFAULT_SIGNAL, label: t("rollout.limits.stopSignalDefault") },
    ...STOP_SIGNALS.map((signal) => ({ value: signal, label: signal })),
  ];

  return (
    <Card title={title} description={t("rollout.limits.description")}>
      <div className="flex flex-col gap-5">
        <Field
          label={t("rollout.limits.historyLabel")}
          description={t("rollout.limits.historyDescription", { min: HISTORY_MIN, max: HISTORY_MAX, server: data.history_keep_server })}
          error={errors.history_keep}
          className="max-w-measure-help"
        >
          <Input mono inputMode="numeric" autoComplete="off" placeholder={t("rollout.limits.historyPlaceholder")} value={draft.history_keep} onValueChange={(value: string) => edit("history_keep", value)} className="w-48" />
        </Field>
        {data.stop_supported ? (
          <>
            <div className="grid gap-5 sm:grid-cols-2">
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
                />
              </Field>
              <Field label={t("rollout.limits.stopSignalLabel")} nativeLabel={false} error={errors.stop_signal}>
                <Select
                  mono
                  options={signalOptions}
                  value={draft.stop_signal}
                  onValueChange={(value) => edit("stop_signal", value)}
                />
              </Field>
            </div>
            <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("rollout.limits.stopApplied")}</p>
          </>
        ) : (
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("rollout.limits.stopUnsupported")}</p>
        )}
        {limits.unplaced !== null ? <ErrorBlock live compact error={limits.unplaced} title={t("rollout.limits.saveFailed")} /> : null}
      </div>
    </Card>
  );
}
