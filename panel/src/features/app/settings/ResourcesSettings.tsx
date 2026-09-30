import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useState } from "react";

import { isApiError, request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { systemInfoQuery } from "../../../api/queries/system";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { useT } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { useAppActions } from "../../apps/useAppActions";
import { changedFields, useSaveBar } from "./formParts";
import { directives, draftOf, parseLimits } from "./limits";
import type { LimitsDraft, LimitsErrors, LimitsValues } from "./limits";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";

type LimitsResult = ResponseOf<"/api/apps/{domain}/limits", "patch">;

/** The request's field names, for a 422 whose `fields` name them. */
const FIELD_OF: Readonly<Record<string, keyof LimitsDraft>> = {
  memory_max_mb: "memory",
  cpu_quota_percent: "cpu",
  tasks_max: "tasks",
};

function sameDraft(a: LimitsDraft, b: LimitsDraft): boolean {
  return a.memory === b.memory && a.cpu === b.cpu && a.tasks === b.tasks;
}

/** What saving did: restarted under the new limits, or rewritten to apply at the next restart. */
function Saved({ result, domain }: { result: LimitsResult; domain: string }) {
  const t = useT();
  const { restart } = useAppActions(domain);
  const units = result.units.join(", ") || t("appSettings.limits.theService");
  return (
    <Notice
      tone="success"
      live
      action={
        result.restart_required ? (
          <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={restart.isPending} onClick={() => restart.mutate()}>
            {t("appSettings.limits.restartNow")}
          </Button>
        ) : undefined
      }
    >
      {result.restarted ? t("appSettings.limits.savedRestarted", { units }) : t("appSettings.limits.savedNotRestarted", { units })}
    </Notice>
  );
}

/**
 * The limits as one form: memory, CPU and processes, each empty for no limit, and whether to
 * restart now so they apply. A restart passes the startup check like any activation, and the
 * old limits are put back if the app does not answer under the new ones. The bounds are checked
 * here for a quick answer and again, authoritatively, by the backend, whose refusal is shown
 * verbatim.
 */
function LimitsForm({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const info = useQuery({ ...systemInfoQuery(), staleTime: 10 * 60_000, refetchOnWindowFocus: false });
  const cores = info.data?.cpu.cores ?? null;

  const current = draftOf(app);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [restart, setRestart] = useState(false);
  const [touched, setTouched] = useState<Partial<Record<keyof LimitsDraft, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const [saved, setSaved] = useState<LimitsResult | null>(null);

  // The app changed underneath an untouched form (saved here, or from a terminal): follow it.
  if (!sameDraft(current, baseline)) {
    setBaseline(current);
    if (sameDraft(draft, baseline)) setDraft(current);
  }

  const parsed = parseLimits(draft, cores, t.locale);
  const save = useMutation({
    mutationFn: (values: LimitsValues & { restart: boolean }) => request("patch", "/api/apps/{domain}/limits", { params: { domain }, body: values }),
    onSuccess: (result) => {
      // What was saved is what the form now says, whatever the app's detail says next.
      const stored = draftOf(result);
      setDraft(stored);
      setBaseline(stored);
      setSaved(result);
      setSubmitted(false);
      setTouched({});
      setRestart(false);
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) =>
        known
          ? { ...known, memory_max_mb: result.memory_max_mb ?? null, cpu_quota_percent: result.cpu_quota_percent ?? null, tasks_max: result.tasks_max ?? null }
          : known,
      );
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    },
  });

  const serverFields: LimitsErrors = {};
  if (isApiError(save.error) && save.error.fields) {
    for (const [name, message] of Object.entries(save.error.fields)) {
      const field = FIELD_OF[name];
      if (field) serverFields[field] = message;
    }
  }
  const errorOf = (field: keyof LimitsDraft): string | undefined =>
    (submitted || touched[field] ? parsed.errors[field] : undefined) ?? serverFields[field];
  const edit = (field: keyof LimitsDraft) => (value: string) => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    setSaved(null);
    if (save.isError) save.reset();
  };
  const blur = (field: keyof LimitsDraft) => () => {
    setTouched((previous) => ({ ...previous, [field]: true }));
  };

  const bar = useSaveBar(
    [
      {
        changes: changedFields(draft, current),
        elevated: true,
        check: () => {
          setSubmitted(true);
          return Object.keys(parsed.errors).length === 0;
        },
        save: async () => {
          await save.mutateAsync({ ...parsed.values, restart });
        },
        discard: () => {
          setDraft(current);
          setTouched({});
          setSubmitted(false);
          setRestart(false);
          save.reset();
        },
      },
    ],
    t("appSettings.limits.notSavedFor", { domain }),
  );

  const now = directives(app);
  return (
    <>
      <Card>
        <div className="flex flex-col gap-5">
          <div className="grid gap-5 sm:grid-cols-3">
            <Field label={t("appSettings.limits.memoryLabel")} description={t("appSettings.limits.memoryDescription")} error={errorOf("memory")}>
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                placeholder={t("appSettings.limits.noLimitPlaceholder")}
                suffix={t("appSettings.limits.memorySuffix")}
                value={draft.memory}
                onValueChange={edit("memory")}
                onBlur={blur("memory")}
              />
            </Field>
            <Field
              label={t("appSettings.limits.cpuLabel")}
              description={cores === null ? t("appSettings.limits.cpuDescriptionUnknown") : t("appSettings.limits.cpuDescriptionKnown", { max: 100 * cores })}
              error={errorOf("cpu")}
            >
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                placeholder={t("appSettings.limits.noLimitPlaceholder")}
                suffix={t("appSettings.limits.cpuSuffix")}
                value={draft.cpu}
                onValueChange={edit("cpu")}
                onBlur={blur("cpu")}
              />
            </Field>
            <Field label={t("appSettings.limits.tasksLabel")} description={t("appSettings.limits.tasksDescription")} error={errorOf("tasks")}>
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                placeholder={t("appSettings.limits.noLimitPlaceholder")}
                value={draft.tasks}
                onValueChange={edit("tasks")}
                onBlur={blur("tasks")}
              />
            </Field>
          </div>
          <Checkbox label={t("appSettings.limits.restartLabel")} description={t("appSettings.limits.restartDescription")} checked={restart} onCheckedChange={setRestart} />
          {save.isError && Object.keys(serverFields).length === 0 ? <ErrorBlock live compact error={save.error} title={t("appSettings.limits.saveFailed")} /> : null}
          {saved !== null ? <Saved result={saved} domain={domain} /> : null}
          <p className="text-12 text-fg-muted">
            {now.length > 0 ? t.rich("appSettings.limits.now", { directives: <Mono>{now.join(" ")}</Mono> }) : t("appSettings.limits.noLimits")}
          </p>
        </div>
      </Card>
      <CommandHint command={`noust app limits ${domain} --memory 512M --cpu 50% --tasks 256`} label={t("appSettings.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
    </>
  );
}

/** Resources: the most this app may use of the server. A static site has no process to limit. */
export function ResourcesSettings() {
  const t = useT();
  const app = useSettingsApp();
  useSubsectionTitle("appSettings.nav.resources", app.domain);
  return (
    <Section title={t("appSettings.limits.title")} description={t("appSettings.limits.description")}>
      {hasUnit(app) ? (
        <LimitsForm app={app} />
      ) : (
        <Card>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("appSettings.limits.staticNote")}</p>
        </Card>
      )}
    </Section>
  );
}
