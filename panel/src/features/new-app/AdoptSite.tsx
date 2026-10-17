/**
 * Adopting a site that already runs on nginx or Apache (checklist B-85; `GET /api/apps/native-sites`
 * and `POST /api/apps/adopt-site`, behind sudo mode). A unit the operator wrote behind the web
 * server, PHP, or a folder of files becomes an app that is listed, whose state and logs are read
 * and whose unit is restarted from here. Nothing is built, written or started: the site, the unit
 * and the files stay exactly as they were, and the preview says so before anything is recorded.
 * The sites found on the server are listed to choose from; the domain can also be typed.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { request } from "../../api/client";
import { nativeSitesQuery } from "../../api/queries/adoption";
import type { NativeSite, SiteAdoptionPlan } from "../../api/queries/adoption";
import { appKeys } from "../../api/queries/apps";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import type { WizardActionsProps } from "../../components/page/Wizard";
import { ErrorBlock } from "../../components/page/QueryState";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Disclosure } from "../../components/ui/Disclosure";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { normalizeDomain } from "../domains/names";
import { splitErrors } from "../settings/formErrors";

export interface AdoptSiteForm {
  domain: string;
  site: string;
  unit: string;
  path: string;
}

const EMPTY: AdoptSiteForm = { domain: "", site: "", unit: "", path: "" };

type SiteErrors = Partial<Record<keyof AdoptSiteForm, string>>;

const FIELDS = ["domain", "site", "unit", "path"] as const;

/** The form's own check, before anything is asked: the one thing there is no finding. */
export function siteProblems(form: AdoptSiteForm, t: (key: "newApp.adoptSite.domainRequired" | "newApp.adoptSite.pathAbsolute") => string): SiteErrors {
  const errors: SiteErrors = {};
  if (form.domain.trim() === "") errors.domain = t("newApp.adoptSite.domainRequired");
  if (form.path.trim() !== "" && !form.path.trim().startsWith("/")) errors.path = t("newApp.adoptSite.pathAbsolute");
  return errors;
}

/** The request, with what was left empty left out: found by looking. */
export function siteBody(form: AdoptSiteForm, preview: boolean) {
  const optional = (value: string): string | undefined => (value.trim() === "" ? undefined : value.trim());
  const site = optional(form.site);
  const unit = optional(form.unit);
  const path = optional(form.path);
  return {
    domain: normalizeDomain(form.domain),
    preview,
    ...(site !== undefined ? { site } : {}),
    ...(unit !== undefined ? { unit } : {}),
    ...(path !== undefined ? { path } : {}),
  };
}

const KIND_KEYS = {
  proxy: "newApp.adoptSite.kind.proxy",
  php: "newApp.adoptSite.kind.php",
  static: "newApp.adoptSite.kind.static",
} as const;

/** The sites the server has that no app records: choose one to fill the form. */
function Found({ onChoose }: { onChoose: (site: NativeSite) => void }) {
  const t = useT();
  const sites = useQuery(nativeSitesQuery());
  if (sites.isPending) return <Skeleton className="h-24 w-full" />;
  if (sites.isError) return <ErrorBlock compact error={sites.error} title={t("newApp.adoptSite.listFailed")} />;
  if (sites.data.sites.length === 0) return <EmptyState variant="inline" title={t("newApp.adoptSite.none")} description={t("newApp.adoptSite.noneDescription")} />;
  return (
    <Card title={t("newApp.adoptSite.foundOnServer")} description={t("newApp.adoptSite.foundOnServerDescription")}>
      <ul aria-label={t("newApp.adoptSite.foundOnServer")} className="flex flex-col divide-y divide-border">
        {sites.data.sites.map((site) => {
          const name = site.domains[0] ?? site.site;
          return (
            <li key={`${site.webserver}/${site.site}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <Mono truncate>{site.domains.join(", ") || site.site}</Mono>
                <span className="text-12 text-pretty text-fg-muted">
                  {site.adoptable
                    ? site.unit !== null
                      ? t("newApp.adoptSite.runsOn", { unit: site.unit })
                      : (site.upstream ?? site.root ?? "")
                    : (site.reason ?? t("newApp.adoptSite.cannot"))}
                </span>
              </div>
              {site.kind !== null ? <Badge>{t(KIND_KEYS[site.kind])}</Badge> : null}
              <Badge mono>{`${site.webserver} ${site.site}`}</Badge>
              <Button size="sm" variant="secondary" disabled={!site.adoptable} aria-label={t("newApp.adoptSite.choose", { domain: name })} onClick={() => onChoose(site)}>
                {t("newApp.adoptSite.chooseShort")}
              </Button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function Plan({ plan, t }: { plan: SiteAdoptionPlan; t: T }) {
  const prose = { mono: false, copy: false } as const;
  const items: KeyValueItem[] = [
    { label: t("newApp.adoptSite.type"), value: t(KIND_KEYS[plan.kind]), ...prose },
    {
      label: t("newApp.adoptSite.site"),
      value: `${plan.webserver} ${plan.site}`,
      hint: plan.site_name ? t("newApp.adoptSite.siteNamed", { name: plan.site_name }) : t("newApp.adoptSite.siteOperator"),
    },
    { label: t("newApp.adoptSite.directory"), value: plan.root, hint: t("newApp.adoptSite.directoryHint") },
    plan.unit !== null
      ? {
          label: t("newApp.adoptSite.unit"),
          value: `${plan.unit}.service`,
          hint: plan.unit_active === false ? t("newApp.adoptSite.unitStopped") : t("newApp.adoptSite.unitHint"),
        }
      : { label: t("newApp.adoptSite.unit"), value: t("newApp.adoptSite.noUnit"), ...prose },
    ...(plan.port !== null ? [{ label: t("newApp.adoptSite.port"), value: plan.port }] : plan.upstream !== null ? [{ label: t("newApp.adoptSite.backend"), value: plan.upstream }] : []),
    { label: t("newApp.adoptSite.tls"), value: plan.ssl ? t("newApp.adoptSite.tlsYes") : t("newApp.adoptSite.tlsNo"), ...prose },
  ];
  return (
    <Card title={t("newApp.adoptSite.planTitle")} description={t("newApp.adoptSite.planDescription")}>
      <div className="flex flex-col gap-4">
        <KeyValueList items={items} />
        {plan.warnings.length > 0 ? (
          <Notice tone="warning" title={t("newApp.adoptSite.warningsTitle")}>
            <ul className="flex list-disc flex-col gap-1 pl-5">
              {plan.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Notice>
        ) : null}
      </div>
    </Card>
  );
}

export interface SiteAdoptionState {
  /** The step's content. */
  content: ReactNode;
  /** The wizard bar's way forward: Preview, then Adopt. */
  actions: WizardActionsProps;
}

/** The site adoption's form and its two presses, for the new-app wizard's source step. */
export function useSiteAdoption(): SiteAdoptionState {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<AdoptSiteForm>(EMPTY);
  const [errors, setErrors] = useState<SiteErrors>({});
  // The preview answers for the form as it was sent; editing it asks again.
  const [previewed, setPreviewed] = useState<{ for: AdoptSiteForm; answer: SiteAdoptionPlan } | null>(null);

  const preview = useMutation({
    mutationFn: (sent: AdoptSiteForm) => request("post", "/api/apps/adopt-site", { body: siteBody(sent, true) }),
    onSuccess: (answer, sent) => {
      setPreviewed({ for: sent, answer });
      announce(t("newApp.adoptSite.previewReady"));
    },
  });
  const adopt = useMutation({
    mutationFn: (sent: AdoptSiteForm) => request("post", "/api/apps/adopt-site", { body: siteBody(sent, false) }),
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      void queryClient.invalidateQueries({ queryKey: ["native-sites"], exact: true });
      announce(t("newApp.adoptSite.adopted", { domain: answer.domain }));
      void navigate({ to: "/apps/$domain", params: { domain: answer.domain } });
    },
  });

  const failure = preview.error ?? adopt.error;
  const split = splitErrors(failure, FIELDS);
  const ready = previewed !== null && JSON.stringify(previewed.for) === JSON.stringify(form);

  const edit = (patch: Partial<AdoptSiteForm>): void => {
    setForm({ ...form, ...patch });
    setErrors({});
    if (preview.isError) preview.reset();
    if (adopt.isError) adopt.reset();
  };

  const choose = (site: NativeSite): void => {
    const domain = site.domains[0] ?? site.site;
    // The file is named only where it is not the domain's own: the finding looks for it otherwise.
    edit({ domain, site: site.site !== domain ? site.site : "", unit: "", path: "" });
  };

  const go = (): void => {
    const found = siteProblems(form, t);
    if (Object.keys(found).length > 0) {
      setErrors(found);
      return;
    }
    if (ready) adopt.mutate(form);
    else preview.mutate(form);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    go();
  };

  const fieldError = (key: keyof AdoptSiteForm): string | undefined => errors[key] ?? split.fields[key];
  const optionalChanged = form.site !== "" || form.unit !== "" || form.path !== "";
  const shownDomain = form.domain.trim() === "" ? "example.com" : normalizeDomain(form.domain);

  const content = (
    <div className="flex flex-col gap-5">
      <Found onChoose={choose} />
      <form id="adopt-site" onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("newApp.adoptSite.domain")} error={fieldError("domain")} description={t("newApp.adoptSite.domainDescription")}>
          <Input mono value={form.domain} onValueChange={(value: string) => edit({ domain: value })} placeholder="blog.example.com" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Disclosure label={t("newApp.adoptSite.moreOptions")} defaultOpen={optionalChanged}>
          <div className="flex flex-col gap-5">
            <Field label={t("newApp.adoptSite.siteField")} optional error={fieldError("site")} description={t("newApp.adoptSite.siteDescription")}>
              <Input mono value={form.site} onValueChange={(value: string) => edit({ site: value })} autoComplete="off" spellCheck={false} />
            </Field>
            <Field label={t("newApp.adoptSite.unitField")} optional error={fieldError("unit")} description={t("newApp.adoptSite.unitDescription")}>
              <Input mono value={form.unit} onValueChange={(value: string) => edit({ unit: value })} placeholder="legacy-api" autoComplete="off" spellCheck={false} />
            </Field>
            <Field label={t("newApp.adoptSite.pathField")} optional error={fieldError("path")} description={t("newApp.adoptSite.pathDescription")}>
              <Input mono value={form.path} onValueChange={(value: string) => edit({ path: value })} placeholder="/srv/legacy" autoComplete="off" spellCheck={false} />
            </Field>
          </div>
        </Disclosure>
      </form>

      {failure !== null && split.form !== null ? <ErrorBlock live compact error={split.form} title={adopt.isError ? t("newApp.adoptSite.notAdopted") : t("newApp.adoptSite.notPreviewed")} /> : null}

      {ready ? <Plan plan={previewed.answer} t={t} /> : null}

      <CommandHint command={`noust app adopt-site ${shownDomain}`} label={t("newApp.source.terminal")} />
    </div>
  );

  return {
    content,
    actions: {
      next: {
        label: ready ? t("newApp.adoptSite.adopt") : t("newApp.adoptSite.preview"),
        loading: preview.isPending || adopt.isPending,
        onClick: go,
      },
    },
  };
}
