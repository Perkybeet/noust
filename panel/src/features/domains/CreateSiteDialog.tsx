import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import type { BodyOf } from "../../api/client";
import { siteTemplatesQuery } from "../../api/queries/sites";
import { getLocale } from "../../app/locale";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { translate, useT } from "../../i18n";
import type { Locale, T } from "../../i18n";
import { cx } from "../../lib/cx";
import { domainProblem, normalizeDomain } from "./names";

type WebServer = "nginx" | "apache";
export type CreateSiteBody = BodyOf<"/api/sites", "post">;

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

/** Every template but the static one names a port the request is proxied to. */
function templateNeedsPort(name: string): boolean {
  return name !== "static";
}

export interface CreateSiteForm {
  domain: string;
  webserver: WebServer;
  template: string;
  port: string;
  ssl: boolean;
  enable: boolean;
}

export type CreateSiteErrors = Partial<Record<"domain" | "port", string>>;

/** The form as the API takes it, or what is wrong with it. */
export function createSiteBody(form: CreateSiteForm, locale: Locale = getLocale()): { body: CreateSiteBody } | { errors: CreateSiteErrors } {
  const errors: CreateSiteErrors = {};
  const domain = normalizeDomain(form.domain);
  const problem = domainProblem(domain, locale);
  if (problem !== null) errors.domain = problem;
  const port = Number(form.port);
  if (templateNeedsPort(form.template) && (!/^\d+$/.test(form.port.trim()) || port < 1 || port > 65535)) {
    errors.port = translate(locale, "domains.createSiteDialog.portRequiredError");
  }
  if (Object.keys(errors).length > 0) return { errors };
  return {
    body: {
      domain,
      webserver: form.webserver,
      template: form.template,
      port: templateNeedsPort(form.template) ? port : 3000,
      ssl: form.ssl,
      enable: form.enable,
    },
  };
}

export interface CreateSiteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The web server this machine runs, which a new site uses unless told otherwise. */
  detected: string;
  /** The site was written: its name and the server's own account of it. */
  onCreated: (site: string, message: string) => void;
}

/**
 * Writes a web server site from one of Noust's templates. With HTTPS asked for, the certificate
 * is ordered first and the site is only rendered with TLS once it exists.
 */
export function CreateSiteDialog({ open, onOpenChange, detected, onCreated }: CreateSiteDialogProps) {
  const t = useT();
  const initial: CreateSiteForm = {
    domain: "",
    webserver: detected === "apache" ? "apache" : "nginx",
    template: "proxy",
    port: "3000",
    ssl: true,
    enable: true,
  };
  const [form, setForm] = useState<CreateSiteForm>(initial);
  const [errors, setErrors] = useState<CreateSiteErrors>({});
  const formId = useId();
  const templateName = useId();

  // Fetched only while the dialog is open: the list rarely changes, and a create dialog that
  // is never opened should never be the reason this call went out.
  const templates = useQuery({ ...siteTemplatesQuery(), enabled: open });
  const available = templates.data?.templates ?? [];

  // The initial guess ("proxy") holds until the real list answers; once it does, whatever the
  // machine actually offers wins if that guess was wrong (a host with no "proxy" template, for
  // instance), without ever storing a value the operator did not choose.
  const template = available.length === 0 || available.includes(form.template) ? form.template : (available[0] ?? form.template);

  const create = useMutation({
    mutationFn: (body: CreateSiteBody) => request("post", "/api/sites", { body }),
    onSuccess: (result) => {
      onCreated(result.site, result.message);
      close(false, true);
    },
  });

  // `settled` closes it from the mutation's own success callback, which runs while the
  // mutation still reads as pending.
  function close(next: boolean, settled = false): void {
    if (!next && create.isPending && !settled) return;
    onOpenChange(next);
    if (!next) {
      setForm(initial);
      setErrors({});
      create.reset();
    }
  }

  const set = (patch: Partial<CreateSiteForm>): void => {
    setForm((current) => ({ ...current, ...patch }));
    setErrors({});
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const result = createSiteBody({ ...form, template }, t.locale);
    if ("errors" in result) {
      setErrors(result.errors);
      return;
    }
    create.mutate(result.body);
  };

  const server = create.error && isApiError(create.error) ? (create.error.fields ?? {}) : {};
  const domain = normalizeDomain(form.domain);

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={t("domains.createSiteDialog.dialogTitle")}
      description={t("domains.createSiteDialog.dialogDescription")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => close(false)}>
            {t("domains.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} disabled={available.length === 0}>
            {form.ssl ? t("domains.createSiteDialog.createSiteAndCertificate") : t("domains.createSite")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("domains.domainFieldLabel")} error={errors.domain ?? server["domain"]}>
          <Input
            mono
            value={form.domain}
            onValueChange={(value: string) => set({ domain: value })}
            placeholder="status.example.com"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </Field>
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
            onValueChange={(webserver) => set({ webserver })}
            className="self-start"
          />
          <span className="text-12 text-fg-muted">{t("domains.createSiteDialog.thisMachineRuns", { webserver: detected })}</span>
        </div>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1.5 text-13 font-medium text-fg">{t("domains.createSiteDialog.templateLegend")}</legend>
          {templates.isError && available.length === 0 ? (
            <ErrorBlock compact error={templates.error} title={t("domains.createSiteDialog.couldNotListTemplates")} onRetry={() => void templates.refetch()} retrying={templates.isRefetching} />
          ) : available.length === 0 ? (
            <div aria-hidden="true" className="grid gap-2 sm:grid-cols-2">
              <Skeleton className="h-16 rounded-control" />
              <Skeleton className="h-16 rounded-control" />
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {available.map((name) => (
                <label
                  key={name}
                  className={cx(
                    "grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] content-start items-start gap-x-2.5 gap-y-0.5 rounded-control border px-3 py-2.5",
                    "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-focus",
                    template === name ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-hover",
                  )}
                >
                  <input
                    type="radio"
                    name={templateName}
                    value={name}
                    checked={template === name}
                    onChange={() => set({ template: name })}
                    className="row-span-2 mt-0.5 size-4 shrink-0 accent-accent"
                  />
                  <span translate="no" className="text-13 font-medium text-fg">
                    {templateLabel(t, name)}
                  </span>
                  <span className="col-start-2 text-12 text-pretty text-fg-muted">{templateDescription(t, name, domain)}</span>
                </label>
              ))}
            </div>
          )}
        </fieldset>
        {templateNeedsPort(template) ? (
          <Field label={t("domains.createSiteDialog.portFieldLabel")} error={errors.port ?? server["port"]} description={t("domains.createSiteDialog.portFieldDescription")} className="sm:max-w-40">
            <Input mono inputMode="numeric" value={form.port} onValueChange={(value: string) => set({ port: value })} />
          </Field>
        ) : null}
        <div className="flex flex-col gap-3">
          <Checkbox
            label={t("domains.createSiteDialog.httpsCheckboxLabel")}
            description={t("domains.createSiteDialog.httpsCheckboxDescription")}
            checked={form.ssl}
            onCheckedChange={(ssl) => set({ ssl })}
          />
          <Checkbox
            label={t("domains.createSiteDialog.enableNowCheckboxLabel")}
            description={t("domains.createSiteDialog.enableNowCheckboxDescription")}
            checked={form.enable}
            onCheckedChange={(enable) => set({ enable })}
          />
        </div>
        {create.isError && Object.keys(server).length === 0 ? (
          <ErrorBlock live compact error={create.error} title={t("domains.createSiteDialog.notCreatedError", { domain: domain || t("domains.theSite") })} />
        ) : null}
      </form>
    </Dialog>
  );
}
