import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { isApiError, request } from "../../api/client";
import { databaseKeys, engineSettingsQuery, enginesQuery } from "../../api/queries/databases";
import type { EngineSetting, EngineSettings, EngineSettingsOutcome } from "../../api/queries/databases";
import { CommandHint } from "../../components/page/CommandHint";
import { DetailPage } from "../../components/page/DetailPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { SaveBar } from "../../components/page/SaveBar";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { engineName, engineState } from "./engines";

/** The value that clears a setting from Noust's file, so the engine's own value applies. */
const DEFAULT_WORD = "default";

/** A Select's value for "Noust does not set it": never one of an engine's own words. */
const UNSET = "__noust_unset__";

type Draft = Readonly<Record<string, string>>;

/** The words an on/off setting is written with on its engine: Redis says yes and no, the others on and off. */
export function booleanWords(setting: EngineSetting): readonly [string, string] {
  const said = [setting.current, setting.configured, setting.recommended].map((value) => value?.toLowerCase());
  return said.some((value) => value === "yes" || value === "no") ? ["yes", "no"] : ["on", "off"];
}

/** What the form holds for one setting: what Noust's file sets, empty when it sets nothing. */
function stored(setting: EngineSetting): string {
  const value = setting.configured ?? "";
  return setting.kind === "boolean" ? value.toLowerCase() : value;
}

/** The form as Noust's file has it now. */
export function draftOf(settings: readonly EngineSetting[]): Draft {
  return Object.fromEntries(settings.map((setting) => [setting.key, stored(setting)]));
}

/**
 * What saving sends: only the settings that differ from Noust's file, an emptied one as
 * `default` so the engine's own value applies again.
 */
export function changedValues(settings: readonly EngineSetting[], draft: Draft): Record<string, string> {
  const values: Record<string, string> = {};
  for (const setting of settings) {
    if (!setting.editable) continue;
    const typed = (draft[setting.key] ?? "").trim();
    if (typed === stored(setting).trim()) continue;
    values[setting.key] = typed === "" ? DEFAULT_WORD : typed;
  }
  return values;
}

/** What applying did, said once: restarted, read again, applied at runtime, or nothing to change. */
function outcomeText(t: T, outcome: EngineSettingsOutcome): string {
  const engine = outcome.display_name;
  switch (outcome.action) {
    case "restart":
      return t("databases.engineSettings.restarted", { engine });
    case "reload":
      return t("databases.engineSettings.reloaded", { engine });
    case "runtime":
      return t("databases.engineSettings.runtime", { engine });
    default:
      return t("databases.engineSettings.unchanged", { engine });
  }
}

/** The lines under a setting: what it does, what it is now, the advice, and what changing it costs. */
function Explanation({ setting, engine, t }: { setting: EngineSetting; engine: string; t: T }) {
  const locked = !setting.editable;
  return (
    <>
      {/* The engine's own explanation, from the server: Noust's words about the engine's setting. */}
      <span className="block max-w-measure-help">{setting.description}</span>
      {setting.kind === "size" && !locked ? <span className="block">{t("databases.engineSettings.sizeHelp")}</span> : null}
      <span className="block">
        {setting.current !== null && setting.current !== undefined
          ? t.rich("databases.engineSettings.now", { value: <Mono>{setting.current}</Mono> })
          : t("databases.engineSettings.notReported", { engine })}{" "}
        {setting.recommended ? t.rich("databases.engineSettings.recommended", { value: <Mono>{setting.recommended}</Mono> }) : null}
      </span>
      {setting.restart && !locked ? <span className="block">{t("databases.engineSettings.restarts", { engine })}</span> : null}
      {locked && setting.locked_reason ? <span className="block max-w-measure-help text-fg">{setting.locked_reason}</span> : null}
    </>
  );
}

/** One setting's control: a list for a closed set of words, else a box, with its unit after it. */
function Control({ setting, value, onChange, t }: { setting: EngineSetting; value: string; onChange: (value: string) => void; t: T }) {
  if (!setting.editable) {
    return <Input mono readOnly value={setting.configured ?? setting.current ?? ""} placeholder={t("databases.engineSettings.notSet")} />;
  }
  const choices = setting.kind === "boolean" ? booleanWords(setting) : (setting.choices ?? []);
  if (choices.length > 0) {
    return (
      <Select
        mono
        value={value === "" ? UNSET : value}
        onValueChange={(next) => onChange(next === UNSET ? "" : next)}
        options={[{ value: UNSET, label: t("databases.engineSettings.notSet") }, ...choices.map((choice) => ({ value: choice, label: choice }))]}
      />
    );
  }
  // A size carries its unit in the value (256MB, 1GB); the other kinds have one fixed unit.
  const suffix = setting.unit && setting.kind !== "size" ? setting.unit : undefined;
  return (
    <Input
      mono
      value={value}
      onValueChange={(next: string) => onChange(next)}
      placeholder={t("databases.engineSettings.notSet")}
      autoComplete="off"
      autoCapitalize="off"
      spellCheck={false}
      {...(suffix !== undefined ? { suffix } : {})}
    />
  );
}

/**
 * The settings as one form: every setting Noust manages on this engine, what the engine uses
 * now, what Noust's file sets (the field), the advice for this server and what changing it
 * costs. Only what changed is sent; a setting emptied is cleared from Noust's file. Opening the
 * engine to the network is asked first, in the server's own words.
 */
function SettingsForm({ engine, data }: { engine: string; data: EngineSettings }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const current = draftOf(data.settings);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [outcome, setOutcome] = useState<EngineSettingsOutcome | null>(null);
  const [exposure, setExposure] = useState<{ warning: string; values: Record<string, string> } | null>(null);

  // Noust's file changed underneath an untouched form (saved here, or from a terminal): follow it.
  const same = (a: Draft, b: Draft): boolean => Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((key) => a[key] === b[key]);
  if (!same(current, baseline)) {
    setBaseline(current);
    if (same(draft, baseline)) setDraft(current);
  }

  const save = useMutation({
    mutationFn: ({ values, confirm }: { values: Record<string, string>; confirm: boolean }) =>
      request("put", "/api/databases/engines/{engine}/settings", { params: { engine }, body: { values, confirm_exposure: confirm } }),
    onSuccess: (result) => {
      setOutcome(result);
      setExposure(null);
      void queryClient.invalidateQueries({ queryKey: databaseKeys.engineSettings(engine) });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.engines, exact: true });
    },
    onError: (error, { values, confirm }) => {
      const warning = isApiError(error) ? error.fields?.["confirm_exposure"] : undefined;
      if (!confirm && warning !== undefined) setExposure({ warning, values });
    },
  });

  const values = changedValues(data.settings, draft);
  const changes = Object.keys(values).length;
  const refusal = isApiError(save.error) ? (save.error.fields ?? {}) : {};
  const asksExposure = refusal["confirm_exposure"] !== undefined;
  const fieldErrors = Object.fromEntries(Object.entries(refusal).filter(([key]) => key !== "confirm_exposure"));
  const failure = save.isError && exposure === null && !asksExposure && Object.keys(fieldErrors).length === 0 ? save.error : null;

  const edit = (key: string, value: string): void => {
    setDraft((previous) => ({ ...previous, [key]: value }));
    setOutcome(null);
    if (save.isError) save.reset();
  };

  const display = data.display_name;
  return (
    <>
      {outcome !== null ? (
        <Notice tone="success" live>
          <span className="flex flex-col gap-1">
            <span>{outcomeText(t, outcome)}</span>
            {outcome.exposed ? <span>{t("databases.engineSettings.exposed", { engine: display })}</span> : null}
            {(outcome.warnings ?? []).map((warning) => (
              <span key={warning}>{warning}</span>
            ))}
          </span>
        </Notice>
      ) : null}
      <Card>
        <div className="flex flex-col gap-5">
          <p className="max-w-measure text-13 text-fg-muted">{t("databases.engineSettings.emptyHelp", { engine: display })}</p>
          <div className="grid gap-5 lg:grid-cols-2">
            {data.settings.map((setting) => {
              const value = draft[setting.key] ?? "";
              const recommendable = setting.editable && setting.recommended !== null && setting.recommended !== undefined && setting.recommended !== "";
              return (
                <Field
                  key={setting.key}
                  label={<Mono>{setting.key}</Mono>}
                  nativeLabel={setting.kind !== "boolean" && (setting.choices ?? []).length === 0}
                  description={<Explanation setting={setting} engine={display} t={t} />}
                  error={fieldErrors[setting.key]}
                  {...(recommendable
                    ? {
                        action: (
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={t("databases.engineSettings.useRecommendedFor", { key: setting.key })}
                            disabled={value.trim().toLowerCase() === (setting.recommended ?? "").toLowerCase()}
                            onClick={() => edit(setting.key, setting.kind === "boolean" ? (setting.recommended ?? "").toLowerCase() : (setting.recommended ?? ""))}
                          >
                            {t("databases.engineSettings.useRecommended")}
                          </Button>
                        ),
                      }
                    : {})}
                >
                  <Control setting={setting} value={value} onChange={(next) => edit(setting.key, next)} t={t} />
                </Field>
              );
            })}
          </div>
          {failure !== null ? <ErrorBlock live compact error={failure} title={t("databases.engineSettings.saveFailed", { engine: display })} /> : null}
        </div>
      </Card>
      <CommandHint command={`noust db settings ${engine}`} label={t("databases.common.fromTerminal")} />
      <SaveBar
        changes={changes}
        saving={save.isPending && exposure === null}
        onSave={() => save.mutate({ values, confirm: false })}
        onDiscard={() => {
          setDraft(current);
          setOutcome(null);
          save.reset();
        }}
      />
      {exposure !== null ? (
        <ConfirmDialog
          open
          friction="simple"
          onOpenChange={(open) => (open ? undefined : setExposure(null))}
          title={t("databases.engineSettings.exposureTitle", { engine: display })}
          description={exposure.warning}
          actionLabel={t("databases.engineSettings.exposureAction")}
          server={node}
          onConfirm={async () => {
            await save.mutateAsync({ values: exposure.values, confirm: true });
          }}
        />
      ) : null}
    </>
  );
}

/**
 * An engine's settings on this server (T2 header, one form saved from its bar): the closed set
 * Noust manages, each with what the engine uses now and the advice for this server's memory
 * and processors. Noust writes them to a file of its own, checks it with the engine's tool and
 * puts the previous settings back if the engine does not answer; that failure is shown with the
 * engine's own words.
 */
export function EngineSettingsPage({ engine }: { engine: string }) {
  const t = useT();
  const { node } = useNode();
  const engines = useQuery(enginesQuery());
  const settings = useQuery(engineSettingsQuery(engine));
  const listed = engines.data?.engines.find((item) => item.name === engine);
  const data = settings.data;
  const display = data?.display_name ?? engineName(engine, engines.data?.engines);
  const running = data?.running ?? listed?.running;
  const state = running === undefined ? null : engineState({ installed: listed?.installed ?? true, running });

  return (
    <DetailPage
      header={{
        title: t("databases.engineSettings.title", { engine: display }),
        breadcrumbs: [
          { label: t("databases.page.title"), to: "/databases" },
          { label: t("databases.tabs.engines"), to: "/databases/engines" },
        ],
        status: state !== null ? <StatusPill state={state.state} label={t(state.label)} /> : <Skeleton className="h-6 w-24 rounded-pill" />,
        ...(data !== undefined
          ? {
              meta: (
                <>
                  <span>{t.rich("databases.engineSettings.fileFact", { file: <Mono tone="muted">{data.file}</Mono> })}</span>
                  <span>{t("databases.engineSettings.resourcesFact", { memory: formatBytes(data.memory_bytes, t.locale), count: data.cpus })}</span>
                </>
              ),
            }
          : {
              meta: (
                <span aria-hidden="true" className="flex h-5 items-center gap-3">
                  <Skeleton className="h-3 w-64" />
                  <Skeleton className="h-3 w-48" />
                </span>
              ),
            }),
        description: t("databases.engineSettings.description", { engine: display }),
        server: node,
      }}
    >
      {settings.isError && data === undefined ? (
        <ErrorBlock error={settings.error} title={t("databases.engineSettings.couldNotLoad", { engine: display })} onRetry={() => void settings.refetch()} retrying={settings.isRefetching} />
      ) : data === undefined ? (
        <LoadingRegion label={t("databases.engineSettings.loading")}>
          <Skeleton className="h-96 w-full rounded-card" />
        </LoadingRegion>
      ) : (
        <div className="flex min-w-0 flex-col gap-6">
          {!data.running ? <Notice title={t("databases.engineSettings.notRunningTitle", { engine: display })}>{t("databases.engineSettings.notRunning")}</Notice> : null}
          <SettingsForm engine={engine} data={data} />
        </div>
      )}
    </DetailPage>
  );
}
