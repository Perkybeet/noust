import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { planManifest } from "../../../api/queries/manifest";
import type { ManifestAction, ManifestPlan } from "../../../api/queries/manifest";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section, Sections } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { SettingsPrimaryAction } from "../SettingsShell";

/** The sentence for an action kind; a kind this console does not know is shown as the server named it. */
function kindLabel(t: T, kind: string): string {
  switch (kind) {
    case "project.create":
      return t("manifestPreview.kind.projectCreate");
    case "environment.create":
      return t("manifestPreview.kind.environmentCreate");
    case "app.create":
      return t("manifestPreview.kind.appCreate");
    case "app.move":
      return t("manifestPreview.kind.appMove");
    case "app.branch":
      return t("manifestPreview.kind.appBranch");
    case "app.limits":
      return t("manifestPreview.kind.appLimits");
    case "app.health":
      return t("manifestPreview.kind.appHealth");
    case "app.domain":
      return t("manifestPreview.kind.appDomain");
    case "app.database":
      return t("manifestPreview.kind.appDatabase");
    case "app.env":
      return t("manifestPreview.kind.appEnv");
    case "app.backups":
      return t("manifestPreview.kind.appBackups");
    default:
      return kind;
  }
}

function columnsFor(t: T): Column<ManifestAction>[] {
  return [
    {
      id: "change",
      header: t("manifestPreview.result.column.change"),
      card: "status",
      width: "w-52",
      cell: (action) => <Badge>{kindLabel(t, action.kind)}</Badge>,
    },
    {
      id: "target",
      header: t("manifestPreview.result.column.target"),
      card: "title",
      cell: (action) => <Mono tone="default">{action.target}</Mono>,
    },
    {
      id: "what",
      header: t("manifestPreview.result.column.what"),
      cell: (action) => (
        <span className="flex flex-col items-start gap-1">
          {/* What will happen, as the planner wrote it: it names the application's own values. */}
          <span className="text-13 text-fg">{action.summary}</span>
          <span className="flex flex-wrap gap-1.5">
            {action.elevated ? <Badge tone="warn">{t("manifestPreview.result.needsSudo")}</Badge> : null}
            {action.unverified ? <Badge>{t("manifestPreview.result.unchecked")}</Badge> : null}
          </span>
        </span>
      ),
    },
  ];
}

function Result({ plan }: { plan: ManifestPlan }) {
  const t = useT();
  const columns = columnsFor(t);
  return (
    <Section title={t("manifestPreview.result.title")} description={plan.empty ? undefined : t("manifestPreview.result.summary", { count: plan.actions.length })}>
      {plan.empty ? (
        <EmptyState variant="inline" title={t("manifestPreview.result.nothing")} description={t("manifestPreview.result.nothingBody")} />
      ) : (
        <DataTable
          caption={t("manifestPreview.result.tableCaption")}
          columns={columns}
          rows={plan.actions}
          getRowId={(action) => `${action.kind}:${action.target}:${action.summary}`}
          mobile="cards"
        />
      )}
      {plan.warnings.length > 0 ? (
        <Notice tone="warning" title={t("manifestPreview.result.warningsTitle")}>
          <ul className="flex flex-col gap-1" translate="no">
            {plan.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
      {plan.unmanaged.length > 0 ? (
        <Notice title={t("manifestPreview.result.unmanagedTitle")}>{t("manifestPreview.result.unmanagedBody", { names: plan.unmanaged.join(", ") })}</Notice>
      ) : null}
      <p className="text-13 text-fg-muted">{t("manifestPreview.result.applyHint")}</p>
    </Section>
  );
}

/**
 * Settings > Manifest: paste the manifest of a server's applications and see what applying it
 * would change here. It is a preview only: applying is the command line's, from the repository
 * the manifest lives in, so the truth is kept in one place and every apply is a token's.
 */
export function ManifestPreview() {
  const t = useT();
  useDocumentTitle(t("manifestPreview.documentTitle"), 1);
  const formId = useId();
  const [text, setText] = useState("");
  const preview = useMutation({ mutationFn: () => planManifest(text) });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!preview.isPending && text.trim() !== "") preview.mutate();
  };

  return (
    <Sections>
      <SettingsPrimaryAction>
        <Button type="submit" form={formId} variant="primary" loading={preview.isPending} disabled={text.trim() === ""}>
          {t("manifestPreview.form.preview")}
        </Button>
      </SettingsPrimaryAction>
      <Section title={t("manifestPreview.intro.title")} description={t("manifestPreview.intro.description")}>
        <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-4">
          {preview.isError ? <ErrorBlock live compact error={preview.error} title={t("manifestPreview.form.errorTitle")} /> : null}
          <Field label={t("manifestPreview.form.label")} description={t("manifestPreview.form.help")}>
            <Textarea
              mono
              rows={14}
              spellCheck={false}
              autoComplete="off"
              placeholder={t("manifestPreview.form.placeholder")}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
            />
          </Field>
        </form>
      </Section>
      {preview.data !== undefined ? <Result plan={preview.data} /> : null}
      <div className="flex flex-col gap-2">
        <CommandHint command={t("manifestPreview.commandPlan")} label={t("manifestPreview.command")} />
        <CommandHint command={t("manifestPreview.commandExport")} />
      </div>
    </Sections>
  );
}
