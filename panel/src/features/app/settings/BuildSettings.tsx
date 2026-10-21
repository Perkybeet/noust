import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { isApiError, request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { allowedKeys, appliedUnit, buildBody, buildProblems, changedKeys, draftOfBuild, hasBuildBlock, looksLikeShell, runtimePresets, sourceOf } from "../../build/model";
import type { AppBuild, BuildDraft, BuildKey } from "../../build/model";
import { BuildCacheCard, ProcessesCard } from "./BuildExtras";
import { SandboxSettings } from "./BuildsSettings";
import { PackagesCard } from "./PackagesCard";
import { useSaveBar } from "./formParts";
import { buildQuery, settingsKeys } from "./queries";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { SETTINGS_ICONS } from "./objectIcons";

const COMMAND_KEYS = ["install", "build", "start"] as const;

/** The variable a start command takes the port from, written so no template mistakes it for its own. */
const PORT_VARIABLE = "$" + "{PORT}";

/** What a command is for and how it runs: one literal key per case, so tsc checks each. */
function commandHelp(t: T, key: (typeof COMMAND_KEYS)[number]): string {
  switch (key) {
    case "install":
      return t("appBuild.fields.installDescription");
    case "build":
      return t("appBuild.fields.buildDescription");
    case "start":
      return t("appBuild.fields.startDescription", { port: PORT_VARIABLE });
  }
}

/** Where the commands of the next deployment come from, and what the unit was last written from. */
function Standing({ build, t }: { build: AppBuild; t: T }) {
  const source = sourceOf(build);
  const unit = appliedUnit(build.applied);
  const items: KeyValueItem[] = [
    {
      label: t("appBuild.standing.source"),
      value: t(source === "operator" ? "appBuild.standing.operator" : source === "repo" ? "appBuild.standing.repo" : "appBuild.standing.none"),
      mono: false,
      copy: false,
      hint: t(source === "operator" ? "appBuild.standing.operatorHint" : source === "repo" ? "appBuild.standing.repoHint" : "appBuild.standing.noneHint"),
    },
    ...(unit?.start !== null && unit?.start !== undefined ? [{ label: t("appBuild.standing.unitStarts"), value: unit.start }] : []),
    ...(unit?.workingDirectory !== null && unit?.workingDirectory !== undefined ? [{ label: t("appBuild.standing.unitDirectory"), value: unit.workingDirectory }] : []),
    ...(unit?.runtime !== null && unit?.runtime !== undefined ? [{ label: t("appBuild.standing.unitRuntime"), value: unit.runtime }] : []),
  ];
  return (
    <Card padding="none">
      <div className="px-4 py-1">
        <KeyValueList items={items} />
      </div>
    </Card>
  );
}

function Form({ build, appType, domain, children }: { build: AppBuild; appType: string; domain: string; children?: ReactNode }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const stored = draftOfBuild(build);
  const [baseline, setBaseline] = useState(build);
  const [draft, setDraft] = useState<BuildDraft>(stored);
  const [submitted, setSubmitted] = useState(false);
  const [removing, setRemoving] = useState(false);
  const source = sourceOf(build);
  const keys = allowedKeys(appType);

  // What applies changed underneath an untouched form (saved here, or from a terminal): follow it.
  if (build !== baseline) {
    setBaseline(build);
    if (changedKeys(draft, draftOfBuild(baseline), appType).length === 0) setDraft(draftOfBuild(build));
  }

  const changes = changedKeys(draft, stored, appType);
  const clientErrors = buildProblems(draft, appType);
  const save = useMutation({
    mutationFn: () => request("put", "/api/apps/{domain}/build", { params: { domain }, body: buildBody(draft, appType) }),
    onSuccess: (result) => {
      queryClient.setQueryData(settingsKeys.build(domain), result);
      setDraft(draftOfBuild(result));
      setBaseline(result);
      setSubmitted(false);
    },
  });
  const serverErrors: Record<string, string> = isApiError(save.error) && save.error.fields !== null ? save.error.fields : {};
  const errorOf = (key: BuildKey): string | undefined => (submitted ? clientErrors[key] : undefined) ?? serverErrors[key] ?? serverErrors[`build.${key}`];
  const placed = Object.keys(serverErrors).length > 0;

  const bar = useSaveBar(
    [
      {
        changes: changes.length,
        // The commands run in the build sandbox and, for `start`, as the application with its secrets.
        elevated: true,
        check: () => {
          setSubmitted(true);
          return Object.keys(clientErrors).length === 0;
        },
        save: async () => {
          await save.mutateAsync();
        },
        discard: () => {
          setDraft(stored);
          setSubmitted(false);
          save.reset();
        },
      },
    ],
    t("appBuild.notSavedFor", { domain }),
  );

  const set = (key: BuildKey) => (value: string) => {
    setDraft((previous) => ({ ...previous, [key]: value }));
    if (save.isError) save.reset();
  };
  const presets = runtimePresets(appType);
  const has = (key: BuildKey): boolean => keys.includes(key);

  return (
    <>
      <Standing build={build} t={t} />
      {build.repository_error !== null && build.repository_error !== undefined ? (
        <Notice tone="error" title={t("appBuild.repositoryError.title")}>
          <div className="flex flex-col gap-2">
            <p>{t("appBuild.repositoryError.body")}</p>
            <SystemOutput label={t("appBuild.repositoryError.label")}>{build.repository_error}</SystemOutput>
          </div>
        </Notice>
      ) : null}
      {source === "repo" ? <InlineMessage>{t("appBuild.fromRepository")}</InlineMessage> : null}

      <Card
        icon={SETTINGS_ICONS.build}
        title={t("appBuild.form.title")}
        description={t("appBuild.form.description")}
        {...(source === "operator"
          ? {
              actions: (
                <Button size="sm" variant="ghost" onClick={() => setRemoving(true)}>
                  {t("appBuild.remove.action")}
                </Button>
              ),
            }
          : {})}
      >
        <div className="flex flex-col gap-5">
          {submitted && clientErrors.form !== undefined ? <Notice tone="error" live>{clientErrors.form}</Notice> : null}
          {has("base_directory") ? (
            <Field label={t("appBuild.fields.base")} optional error={errorOf("base_directory")} description={t("appBuild.fields.baseDescription")} className="sm:max-w-96">
              <Input mono value={draft.base_directory} placeholder="apps/web" onValueChange={set("base_directory")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
          ) : null}
          {COMMAND_KEYS.filter(has).map((key) => (
            <div key={key} className="flex flex-col gap-1.5">
              <Field
                label={t(key === "install" ? "appBuild.fields.install" : key === "build" ? "appBuild.fields.build" : "appBuild.fields.start")}
                optional
                error={errorOf(key)}
                description={commandHelp(t, key)}
              >
                <Input
                  mono
                  value={draft[key]}
                  placeholder={key === "install" ? "pnpm install --frozen-lockfile" : key === "build" ? "pnpm --filter web build" : "node dist/server.js --port ${PORT}"}
                  onValueChange={set(key)}
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                />
              </Field>
              {looksLikeShell(draft[key]) ? <InlineMessage>{t("appBuild.fields.shellWarning")}</InlineMessage> : null}
            </div>
          ))}
          {has("publish_directory") ? (
            <Field label={t("appBuild.fields.publish")} optional error={errorOf("publish_directory")} description={t("appBuild.fields.publishDescription")} className="sm:max-w-96">
              <Input mono value={draft.publish_directory} placeholder="dist" onValueChange={set("publish_directory")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
          ) : null}
          {has("runtime") ? (
            <div className="flex flex-col gap-2">
              <Field label={t("appBuild.fields.runtime")} optional error={errorOf("runtime")} description={t("appBuild.fields.runtimeDescription")} className="sm:max-w-96">
                <Input mono value={draft.runtime} placeholder={presets[0] ?? "node@22"} onValueChange={set("runtime")} autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </Field>
              <div role="group" aria-label={t("appBuild.fields.runtimePresets")} className="flex flex-wrap items-center gap-1.5">
                {presets.map((preset) => (
                  <Button key={preset} size="sm" variant="ghost" aria-pressed={draft.runtime.trim() === preset} onClick={() => set("runtime")(preset)}>
                    <Mono>{preset}</Mono>
                  </Button>
                ))}
              </div>
            </div>
          ) : null}
          {has("timeout") ? (
            <Field label={t("appBuild.fields.timeout")} optional error={errorOf("timeout")} description={t("appBuild.fields.timeoutDescription")} className="sm:max-w-64">
              <Input mono inputMode="numeric" value={draft.timeout} placeholder="3600" onValueChange={set("timeout")} autoComplete="off" spellCheck={false} />
            </Field>
          ) : null}
        </div>
      </Card>
      {save.isError && !placed ? <ErrorBlock live compact error={save.error} title={t("appBuild.saveFailed", { domain })} /> : null}
      {children}
      <CommandHint command={`noust app build set ${domain} --start 'node dist/server.js' --runtime node@22`} label={t("appSettings.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        server={node}
        title={t("appBuild.remove.title", { domain })}
        description={t("appBuild.remove.description")}
        actionLabel={t("appBuild.remove.action")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/build", { params: { domain } });
          queryClient.setQueryData(settingsKeys.build(domain), result);
          setDraft(draftOfBuild(result));
          setBaseline(result);
        }}
      />
    </>
  );
}

/**
 * Build (T3 subsection): how the application is made. The install, build and start commands, the
 * base directory a project lives in, the directory a static build leaves and the runtime it runs on,
 * for the next deployment; where they come from (the operator's own block, which wins whole, the
 * repository's `noust.yaml`, or neither) and what the service was last written from. Beside them the
 * sandbox the builds run in, the system packages they need, the cache they share and the process
 * types the code declares. Writing the block is root-equivalent (its commands run in the build
 * sandbox and, for `start`, as the application), so saving asks to confirm it is you.
 */
export function BuildSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  const appType = app.app_type ?? "";
  useSubsectionTitle("appSettings.nav.build", domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.build");
  const supported = hasBuildBlock(appType);
  const build = useQuery({ ...buildQuery(domain), enabled: supported });
  const rest = (
    <>
      <SandboxSettings app={app} />
      {supported ? (
        <>
          <PackagesCard domain={domain} />
          <BuildCacheCard domain={domain} />
          <ProcessesCard domain={domain} />
        </>
      ) : null}
    </>
  );

  const form = supported && build.data !== undefined ? build.data : null;

  return (
    <Section title={t("appBuild.title")} {...headerIcon} description={t("appBuild.description")}>
      {!supported ? (
        <EmptyState variant="inline" title={t("appBuild.notSupported", { type: appType })} />
      ) : build.isError && form === null ? (
        <ErrorBlock error={build.error} title={t("appBuild.readFailed")} onRetry={() => void build.refetch()} retrying={build.isRefetching} />
      ) : form === null ? (
        <LoadingRegion label={t("appBuild.loading")}>
          <SkeletonCard lines={6} />
        </LoadingRegion>
      ) : null}
      {form !== null ? (
        <Form build={form} appType={appType} domain={domain}>
          {rest}
        </Form>
      ) : (
        rest
      )}
    </Section>
  );
}
