import { useId } from "react";
import type { Ref, SyntheticEvent } from "react";

import type { AppExportDocument } from "../../api/queries/appImport";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { parseTimestamp, formatDateTime } from "../../lib/format";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { AddressFields } from "./AddressFields";
import { addressItem } from "./DeployStep";
import { ReviewGroup } from "./ReviewStep";
import { useDomainDnsCheck } from "./useDomainDnsCheck";
import { missingValues, secretField, sourceStripped } from "./exportFile";
import type { ImportForm } from "./exportFile";
import { joinList, typeName } from "./wizard";
import type { AppTypeOption, ReviewErrors } from "./wizard";

/** What an export defines, one fact per row, as it was exported (its source already redacted by the server). */
export function exportFacts(t: T, document: AppExportDocument, types: readonly AppTypeOption[]): KeyValueItem[] {
  const app = document.app;
  const names = [...(document.domains?.aliases ?? []), ...(document.domains?.redirects ?? [])];
  const cron = document.cron ?? [];
  const backup = document.backup ?? null;
  const previews = document.previews ?? null;
  const env = Object.values(document.env ?? {});
  const missing = missingValues(document).length;
  const exportedAt = parseTimestamp(document.exported_at ?? null);
  const destinations = backup?.destinations?.map((destination) => destination.name) ?? [];
  return [
    { label: t("newApp.importApp.exportedFrom"), value: app.domain },
    { label: t("newApp.importApp.type"), value: typeName(types, app.app_type), mono: false, copy: false },
    ...(app.source ? [{ label: t("newApp.importApp.source"), value: app.source, copy: false as const }] : []),
    ...(app.branch ? [{ label: t("newApp.importApp.branch"), value: app.branch }] : []),
    ...(app.layout
      ? [{ label: t("newApp.importApp.deploys"), value: t(app.layout === "inplace" ? "newApp.importApp.inplace" : "newApp.importApp.releases"), mono: false, copy: false as const }]
      : []),
    {
      label: t("newApp.importApp.otherNames"),
      value: names.length > 0 ? joinList(names, t.locale) : t("newApp.importApp.none"),
      mono: names.length > 0,
      copy: false,
    },
    {
      label: t("newApp.importApp.cron"),
      value: cron.length > 0 ? t("newApp.importApp.cronCount", { count: cron.length }) : t("newApp.importApp.none"),
      mono: false,
      copy: false,
      ...(cron.length > 0 ? { hint: joinList(cron.map((job) => job.name), t.locale) } : {}),
    },
    {
      label: t("newApp.importApp.backup"),
      value: backup !== null ? t("newApp.importApp.backupSchedule", { schedule: backup.schedule }) : t("newApp.importApp.noBackup"),
      mono: false,
      copy: false,
      ...(destinations.length > 0 ? { hint: joinList(destinations, t.locale) } : {}),
    },
    {
      label: t("newApp.importApp.previews"),
      value: previews !== null ? t("newApp.importApp.previewsOn", { domain: previews.base_domain }) : t("newApp.importApp.off"),
      mono: false,
      copy: false,
    },
    {
      label: t("newApp.importApp.zeroDowntime"),
      value: t(app.zero_downtime?.enabled === true ? "newApp.importApp.on" : "newApp.importApp.off"),
      mono: false,
      copy: false,
    },
    {
      label: t("newApp.importApp.environment"),
      value: t("newApp.importApp.variables", { count: env.length }),
      mono: false,
      copy: false,
      ...(missing > 0 ? { hint: t("newApp.importApp.missing", { count: missing }) } : {}),
    },
    ...(exportedAt !== null
      ? [
          {
            label: t("newApp.importApp.exported"),
            value: formatDateTime(exportedAt, t.locale),
            mono: false,
            copy: false as const,
            ...(document.wasm_version ? { hint: t("newApp.importApp.exportedBy", { version: document.wasm_version }) } : {}),
          },
        ]
      : []),
  ];
}

/** What the import is about to do, one fact per row. */
export function importSummary(t: T, document: AppExportDocument, form: ImportForm, types: readonly AppTypeOption[]): KeyValueItem[] {
  const names = (document.domains?.aliases?.length ?? 0) + (document.domains?.redirects?.length ?? 0);
  const cron = document.cron?.length ?? 0;
  const applies = [
    ...(names > 0 ? [t("newApp.importApp.applies.names", { count: names })] : []),
    ...(cron > 0 ? [t("newApp.importApp.applies.cron", { count: cron })] : []),
    ...(document.backup ? [t("newApp.importApp.applies.backup")] : []),
    ...(document.previews ? [t("newApp.importApp.applies.previews")] : []),
    ...(document.app.zero_downtime?.enabled === true ? [t("newApp.importApp.applies.zeroDowntime")] : []),
  ];
  return [
    { label: t("newApp.importApp.summarySource"), value: form.source.trim(), copy: false },
    { label: t("newApp.importApp.type"), value: typeName(types, document.app.app_type), mono: false, copy: false },
    addressItem(t, { domain: form.domain, ssl: document.app.ssl, includeWww: document.app.include_www }),
    {
      label: t("newApp.importApp.summaryApplies"),
      value: applies.length > 0 ? joinList(applies, t.locale) : t("newApp.importApp.appliesNothing"),
      mono: false,
      copy: false,
    },
  ];
}

export interface ImportReviewProps {
  document: AppExportDocument;
  types: readonly AppTypeOption[];
  form: ImportForm;
  errors: ReviewErrors;
  onChange: (form: ImportForm) => void;
  onBack: () => void;
  onContinue: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}

/**
 * Step two for an import: what the export defines, where it is created (the domain, the
 * source, which has to be given again when the export took its credentials out) and the
 * values of the secrets it left out, every one of them required.
 */
export function ImportReview({ document, types, form, errors, onChange, onBack, onContinue, headingRef }: ImportReviewProps) {
  const t = useT();
  const dns = useDomainDnsCheck(form.domain);
  const summaryId = useId();
  const missing = Object.keys(form.secrets);
  const stripped = sourceStripped(document.app.source ?? "");
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    dns.checkNow();
    onContinue();
  };

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 ref={headingRef} tabIndex={-1} className="title text-18 text-fg outline-none">
          {t("newApp.review.heading")}
        </h2>
        <p className="text-14 text-pretty text-fg-muted">{t("newApp.importApp.reviewIntro")}</p>
      </header>

      <section aria-labelledby={summaryId} className="flex flex-col gap-1 rounded-card border border-border bg-surface px-4 py-3 shadow-raised">
        <h3 id={summaryId} className="text-13 font-medium text-fg">
          {t("newApp.importApp.summary")}
        </h3>
        <KeyValueList items={exportFacts(t, document, types)} />
      </section>

      <ReviewGroup title={t("newApp.importApp.where")}>
        <div className="flex flex-col gap-5">
          <AddressFields
            value={{ domain: form.domain, includeWww: false, ssl: document.app.ssl }}
            onChange={(patch) => {
              if (patch.domain !== undefined) onChange({ ...form, domain: patch.domain });
            }}
            error={errors["domain"]}
            dns={dns}
            description={t("newApp.importApp.domainDescription")}
            options={false}
          />
          <Field
            label={t("newApp.importApp.sourceField")}
            error={errors["source"]}
            description={t(stripped ? "newApp.importApp.sourceStripped" : "newApp.importApp.sourceDescription")}
          >
            <Input
              mono
              value={form.source}
              onValueChange={(value: string) => onChange({ ...form, source: value })}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
          </Field>
        </div>
      </ReviewGroup>

      {missing.length > 0 ? (
        <ReviewGroup title={t("newApp.importApp.secrets")} description={t("newApp.importApp.secretsDescription")}>
          <div className="flex flex-col gap-4">
            {missing.map((name) => (
              <Field
                key={name}
                label={
                  <span className="flex flex-wrap items-center gap-x-2">
                    <code translate="no" className="text-12 font-medium text-fg">
                      {name}
                    </code>
                    <span className="text-12 font-medium text-fg-muted">{t("newApp.env.required")}</span>
                  </span>
                }
                error={errors[secretField(name)]}
              >
                <Input
                  mono
                  type="password"
                  value={form.secrets[name] ?? ""}
                  onValueChange={(value: string) => onChange({ ...form, secrets: { ...form.secrets, [name]: value } })}
                  autoComplete="new-password"
                  autoCapitalize="off"
                  spellCheck={false}
                />
              </Field>
            ))}
          </div>
        </ReviewGroup>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-6">
        <Button onClick={onBack}>{t("newApp.review.back")}</Button>
        <Button type="submit" variant="primary">
          {t("newApp.review.continue")}
        </Button>
      </div>
    </form>
  );
}
