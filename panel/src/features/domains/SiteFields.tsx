import { useQuery } from "@tanstack/react-query";

import { siteTemplatesQuery } from "../../api/queries/sites";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { normalizeDomain } from "./names";
import { templateNeedsPort } from "./siteForm";
import type { CreateSiteErrors, CreateSiteForm, WebServer } from "./siteForm";

/**
 * Copy for the templates Noust ships, keyed by name. `GET /api/sites/templates` is the source
 * of truth for which templates exist; this only dresses up the ones it recognises. A template
 * this machine offers but this map does not know yet still shows, titled from its own name.
 */
function templateCopy(t: T): Readonly<Record<string, { label: string; description: (domain: string) => string }>> {
  return {
    proxy: { label: t("domains.createSiteDialog.templateReverseProxyLabel"), description: () => t("domains.createSiteDialog.templateReverseProxyDescription") },
    static: {
      label: t("domains.createSiteDialog.templateStaticLabel"),
      description: (domain) => t("domains.createSiteDialog.templateStaticDescription", { domain: domain || "<domain>" }),
    },
    advanced: { label: t("domains.createSiteDialog.templateAdvancedLabel"), description: () => t("domains.createSiteDialog.templateAdvancedDescription") },
    monorepo: { label: t("domains.createSiteDialog.templateMonorepoLabel"), description: () => t("domains.createSiteDialog.templateMonorepoDescription") },
  };
}

function templateLabel(t: T, name: string): string {
  return templateCopy(t)[name]?.label ?? (name.charAt(0).toUpperCase() + name.slice(1));
}

function templateDescription(t: T, name: string, domain: string): string {
  return templateCopy(t)[name]?.description(domain) ?? t("domains.createSiteDialog.templateUnknownDescription", { name });
}

/**
 * The template a form holds, given what the machine offers: the initial guess ("proxy") holds until the real list answers;
 * once it does, whatever the machine actually offers wins if that guess was wrong (a host with no "proxy" template, for
 * instance), without ever storing a value the operator did not choose.
 */
export function useSiteTemplate(form: CreateSiteForm, enabled: boolean): { template: string; available: readonly string[]; query: ReturnType<typeof useSiteTemplates> } {
  const query = useSiteTemplates(enabled);
  const available = query.data?.templates ?? [];
  const template = available.length === 0 || available.includes(form.template) ? form.template : (available[0] ?? form.template);
  return { template, available, query };
}

/** Fetched only while a site is being made: the list rarely changes, and a dialog never opened is no reason for this call. */
function useSiteTemplates(enabled: boolean) {
  return useQuery({ ...siteTemplatesQuery(), enabled });
}

export interface SiteFieldsProps {
  form: CreateSiteForm;
  errors: CreateSiteErrors;
  /** What the server said of a field, by its name. */
  server: Readonly<Record<string, string>>;
  onChange: (patch: Partial<CreateSiteForm>) => void;
  /** The web server this machine runs, which a new site uses unless told otherwise. */
  detected: string;
  /** The template that holds (see `useSiteTemplate`) and what the machine offers. */
  template: string;
  available: readonly string[];
  templates: ReturnType<typeof useSiteTemplates>;
}

/**
 * What a site of its own asks beyond its name: the web server, which template it is written from, the port a proxy goes
 * to, whether HTTPS is ordered with it and whether it is enabled at once. With HTTPS asked for, the certificate is ordered
 * first and the site is only rendered with TLS once it exists.
 */
export function SiteFields({ form, errors, server, onChange, detected, template, available, templates }: SiteFieldsProps) {
  const t = useT();
  const domain = normalizeDomain(form.domain);
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <span aria-hidden="true" className="text-13 font-medium text-fg">
          {t("domains.webServerLabel")}
        </span>
        <SegmentedControl<WebServer>
          label={t("domains.webServerLabel")}
          options={[
            { value: "nginx", label: "nginx" },
            { value: "apache", label: "Apache" },
          ]}
          value={form.webserver}
          onValueChange={(webserver) => onChange({ webserver })}
          className="self-start"
        />
        <span className="text-12 text-fg-muted">{t("domains.createSiteDialog.thisMachineRuns", { webserver: detected })}</span>
      </div>
      {templates.isError && available.length === 0 ? (
        <ErrorBlock compact error={templates.error} title={t("domains.createSiteDialog.couldNotListTemplates")} onRetry={() => void templates.refetch()} retrying={templates.isRefetching} />
      ) : available.length === 0 ? (
        <div aria-hidden="true" className="grid gap-2 sm:grid-cols-2">
          <Skeleton className="h-16 rounded-control" />
          <Skeleton className="h-16 rounded-control" />
        </div>
      ) : (
        <ChoiceCards
          legend={t("domains.createSiteDialog.templateLegend")}
          options={available.map((name) => ({
            value: name,
            label: templateLabel(t, name),
            description: templateDescription(t, name, domain),
            untranslated: true,
          }))}
          value={template}
          onValueChange={(name) => onChange({ template: name })}
        />
      )}
      {templateNeedsPort(template) ? (
        <Field label={t("domains.createSiteDialog.portFieldLabel")} error={errors.port ?? server["port"]} description={t("domains.createSiteDialog.portFieldDescription")} className="sm:max-w-40">
          <Input mono inputMode="numeric" value={form.port} onValueChange={(value: string) => onChange({ port: value })} />
        </Field>
      ) : null}
      <div className="flex flex-col gap-3">
        <Checkbox
          label={t("domains.createSiteDialog.httpsCheckboxLabel")}
          description={t("domains.createSiteDialog.httpsCheckboxDescription")}
          checked={form.ssl}
          onCheckedChange={(ssl) => onChange({ ssl })}
        />
        <Checkbox
          label={t("domains.createSiteDialog.enableNowCheckboxLabel")}
          description={t("domains.createSiteDialog.enableNowCheckboxDescription")}
          checked={form.enable}
          onCheckedChange={(enable) => onChange({ enable })}
        />
      </div>
    </div>
  );
}
