import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { useSaveBar } from "./formParts";
import type { FormPart } from "./formParts";
import { hooksQuery, olderServer, settingsKeys } from "./queries";
import type { AppHooks } from "./queries";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";

type Hook = AppHooks["pre_deploy"][number];

/** An example of the document, for an empty field: the shape of a noust.yaml holding only hooks. */
const EXAMPLE = ["hooks:", "  pre_deploy:", "    - run: npx prisma migrate deploy", "      migrates: true", "  post_deploy:", "    - run: ./scripts/purge-cache.sh"].join("\n");

/** Where the hooks that apply come from, in words. */
function originWords(t: T, source: string): { badge: string; sentence: string } {
  switch (source) {
    case "operator":
      return { badge: t("appSettings.hooks.fromOperator"), sentence: t("appSettings.hooks.fromOperatorBody") };
    case "repo":
      return { badge: t("appSettings.hooks.fromRepository"), sentence: t("appSettings.hooks.fromRepositoryBody") };
    default:
      return { badge: t("appSettings.hooks.none"), sentence: t("appSettings.hooks.noneBody") };
  }
}

function HookList({ label, hooks, t }: { label: string; hooks: readonly Hook[]; t: T }) {
  if (hooks.length === 0) return <p className="text-13 text-fg-muted">{t("appSettings.hooks.emptyPhase")}</p>;
  return (
    <ol aria-label={label} className="flex flex-col divide-y divide-border rounded-control border border-border">
      {hooks.map((hook, index) => {
        const run = hook.run.join(" ");
        return (
          <li key={`${run}-${String(index)}`} className="flex min-w-0 flex-col gap-1 px-3 py-2">
            <Mono truncate title={run}>
              {run}
            </Mono>
            <span className="flex flex-wrap gap-x-3 text-12 text-fg-muted">
              {hook.service ? <span>{t("appSettings.hooks.inService", { service: hook.service })}</span> : null}
              {hook.workdir ? <span>{t.rich("appSettings.hooks.inDirectory", { path: <Mono>{hook.workdir}</Mono> })}</span> : null}
              <span>{t("appSettings.hooks.timeout", { count: hook.timeout })}</span>
              {hook.migrates ? <span>{t("appSettings.hooks.migrates")}</span> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** The operator's document as a part of the subsection's one form, saved with PUT (root-equivalent). */
function useDocumentPart(domain: string, stored: string) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(stored);
  const [baseline, setBaseline] = useState(stored);
  if (stored !== baseline) {
    setBaseline(stored);
    if (draft === baseline) setDraft(stored);
  }
  const save = useMutation({
    mutationFn: (document: string) => request("put", "/api/apps/{domain}/hooks", { params: { domain }, body: { document } }),
    onSuccess: (saved) => {
      queryClient.setQueryData(settingsKeys.hooks(domain), saved);
      setDraft(saved.document ?? "");
      setBaseline(saved.document ?? "");
    },
  });
  const split = splitErrors(save.error, ["document"] as const, "document");
  const part: FormPart = {
    changes: draft.trim() !== stored.trim() ? 1 : 0,
    elevated: true,
    check: () => true,
    save: async () => {
      await save.mutateAsync(draft);
    },
    discard: () => {
      setDraft(stored);
      save.reset();
    },
  };
  return {
    part,
    draft,
    edit: (value: string) => {
      setDraft(value);
      if (save.isError) save.reset();
    },
    error: split.fields.document,
    failure: save.isError ? split.form : null,
  };
}

function OperatorHooks({ domain, hooks, t }: { domain: string; hooks: AppHooks; t: T }) {
  const queryClient = useQueryClient();
  const { node } = useNode();
  const [removing, setRemoving] = useState(false);
  const document = useDocumentPart(domain, hooks.source === "operator" ? (hooks.document ?? "") : "");
  const bar = useSaveBar([document.part], t("appSettings.hooks.notSaved", { domain }));
  return (
    <>
      <Card
        title={t("appSettings.hooks.operatorTitle")}
        description={t("appSettings.hooks.operatorDescription")}
        actions={
          hooks.source === "operator" ? (
            <Button size="sm" variant="ghost" onClick={() => setRemoving(true)}>
              {t("appSettings.hooks.remove")}
            </Button>
          ) : undefined
        }
      >
        <div className="flex flex-col gap-3">
          <Field label={t("appSettings.hooks.documentLabel")} description={t("appSettings.hooks.documentDescription")} error={document.error}>
            <Textarea
              mono
              rows={10}
              wrap="off"
              autoComplete="off"
              spellCheck={false}
              placeholder={EXAMPLE}
              value={document.draft}
              onChange={(event) => document.edit(event.target.value)}
            />
          </Field>
          {document.failure !== null ? <ErrorBlock live compact error={document.failure} title={t("appSettings.hooks.notSaved", { domain })} /> : null}
          <CommandHint command={`noust app hooks set ${domain} --file hooks.yaml`} label={t("appSettings.fromTerminal")} />
        </div>
      </Card>
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        server={node}
        title={t("appSettings.hooks.removeTitle", { domain })}
        description={t("appSettings.hooks.removeDescription")}
        actionLabel={t("appSettings.hooks.remove")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/hooks", { params: { domain } });
          queryClient.setQueryData(settingsKeys.hooks(domain), result);
        }}
      />
    </>
  );
}

/**
 * Deploy hooks (spec 3.2, section 1.1): what runs before the new version serves and after it
 * does, where it comes from - the repository's noust.yaml, or the operator's own, which
 * replaces it whole - and the operator's document, edited here. Writing it is root-equivalent:
 * a hook runs on the server, so saving asks to confirm it's you and may wait for a second
 * person.
 */
export function HooksSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.hooks", domain);
  const isStatic = app.app_type === "static";
  const hooks = useQuery({ ...hooksQuery(domain), enabled: !isStatic });

  if (isStatic) {
    return (
      <Section title={t("appSettings.hooks.title")} description={t("appSettings.hooks.description")}>
        <EmptyState variant="inline" title={t("appSettings.hooks.staticNone")} />
      </Section>
    );
  }

  return (
    <Section title={t("appSettings.hooks.title")} description={t("appSettings.hooks.description")}>
      {hooks.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <span className="sr-only">{t("appSettings.hooks.loading")}</span>
          <Skeleton className="h-32 w-full rounded-card" />
        </div>
      ) : hooks.isError && olderServer(hooks.error) ? (
        <EmptyState variant="inline" title={t("appSettings.hooks.olderServer")} />
      ) : hooks.isError ? (
        <ErrorBlock error={hooks.error} title={t("appSettings.hooks.readFailed")} onRetry={() => void hooks.refetch()} retrying={hooks.isRefetching} />
      ) : (
        <>
          <Card
            title={t("appSettings.hooks.inUseTitle")}
            actions={<Badge>{originWords(t, hooks.data.source).badge}</Badge>}
          >
            <div className="flex flex-col gap-4">
              <p className="max-w-measure text-13 text-pretty text-fg-muted">{originWords(t, hooks.data.source).sentence}</p>
              {hooks.data.repository_error ? (
                <Notice tone="error" title={t("appSettings.hooks.repositoryErrorTitle")}>
                  <div className="flex flex-col gap-2">
                    <p>{t("appSettings.hooks.repositoryErrorBody")}</p>
                    <SystemOutput label={t("appSettings.hooks.repositoryErrorLabel")}>{hooks.data.repository_error}</SystemOutput>
                  </div>
                </Notice>
              ) : null}
              <div className="flex flex-col gap-2">
                <p className="text-13 font-medium text-fg">{t("appSettings.hooks.preDeploy")}</p>
                <HookList label={t("appSettings.hooks.preDeploy")} hooks={hooks.data.pre_deploy} t={t} />
              </div>
              <div className="flex flex-col gap-2">
                <p className="text-13 font-medium text-fg">{t("appSettings.hooks.postDeploy")}</p>
                <HookList label={t("appSettings.hooks.postDeploy")} hooks={hooks.data.post_deploy} t={t} />
              </div>
            </div>
          </Card>
          <OperatorHooks domain={domain} hooks={hooks.data} t={t} />
        </>
      )}
    </Section>
  );
}
