import { ChevronDown } from "lucide-react";

import type { AppExportDocument } from "../../api/queries/appImport";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { parseTimestamp, formatDateTime } from "../../lib/format";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { AddressFields } from "./AddressFields";
import { addressItem } from "./DeployStep";
import type { DomainDnsCheck } from "./useDomainDnsCheck";
import { missingValues, secretField, sourceStripped } from "./exportFile";
import type { ImportForm } from "./exportFile";
import { joinList, typeName } from "./wizard";
import type { AppTypeOption, ReviewErrors } from "./wizard";

/** Whether the export turns something on, as a state at a glance. */
function OnOff({ t, on }: { t: T; on: boolean }) {
  return <StatusPill state={on ? "running" : "stopped"} label={on ? t("newApp.importApp.on") : t("newApp.importApp.off")} size="sm" />;
}

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
    // What the export turns on, at a glance (item 56): on in the running green, off in grey.
    {
      label: t("newApp.importApp.previews"),
      value: <OnOff t={t} on={previews !== null} />,
      ...(previews !== null ? { hint: t("newApp.importApp.previewsOn", { domain: previews.base_domain }) } : {}),
      mono: false,
      copy: false,
    },
    {
      label: t("newApp.importApp.zeroDowntime"),
      value: <OnOff t={t} on={app.zero_downtime?.enabled === true} />,
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

export interface ImportAddressProps {
  document: AppExportDocument;
  types: readonly AppTypeOption[];
  form: ImportForm;
  errors: ReviewErrors;
  onChange: (form: ImportForm) => void;
  dns: DomainDnsCheck;
}

/**
 * Where an export is created: what it defines, folded under one line, then the domain (the
 * exported one, or another) and the source, which has to be given again when the export took
 * its credentials out.
 */
export function ImportAddress({ document, types, form, errors, onChange, dns }: ImportAddressProps) {
  const t = useT();
  const stripped = sourceStripped(document.app.source ?? "");
  return (
    <div className="flex flex-col gap-6">
      <details className="group overflow-hidden rounded-card border border-border">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 -outline-offset-2 hover:bg-surface-hover [&::-webkit-details-marker]:hidden">
          <span className="text-13 font-medium text-fg">{t("newApp.importApp.summary")}</span>
          <ChevronDown aria-hidden="true" className="size-icon-sm text-fg-muted transition-transform duration-(--duration-fast) group-open:rotate-180" />
        </summary>
        <div className="border-t border-border px-4 py-1">
          <KeyValueList items={exportFacts(t, document, types)} />
        </div>
      </details>
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
  );
}

export interface ImportSecretsProps {
  form: ImportForm;
  errors: ReviewErrors;
  onChange: (form: ImportForm) => void;
}

/** The values of the secrets an export left out, every one of them required. */
export function ImportSecrets({ form, errors, onChange }: ImportSecretsProps) {
  const t = useT();
  const missing = Object.keys(form.secrets);
  if (missing.length === 0) return <p className="text-13 text-fg-muted">{t("newApp.importApp.noSecrets")}</p>;
  return (
    <div className="flex flex-col gap-5">
      {missing.map((name) => (
        <Field key={name} label={<Mono>{name}</Mono>} error={errors[secretField(name)]}>
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
  );
}
