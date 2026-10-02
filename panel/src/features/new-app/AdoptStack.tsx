/**
 * Adopting a Docker Compose stack that already runs (spec 3.2, section 1.4; `POST
 * /api/apps/adopt`, behind sudo mode). Nothing is cloned, cleaned or started: Noust reads the
 * project from the running containers, finds the site that serves the domain, and rehearses
 * `docker compose up --dry-run` to prove it would change nothing. The preview comes first and
 * shows that rehearsal verbatim; adopting is the second press, once it has been read. A
 * rehearsal that would recreate something is refused with Compose's own output, and adopting
 * anyway is a choice the operator ticks after reading it.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { SystemOutput } from "../../components/ui/SystemOutput";
import type { WizardActionsProps } from "../../components/page/Wizard";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { normalizeDomain } from "../domains/names";

export type Adoption = ResponseOf<"/api/apps/adopt", "post">;

export interface AdoptForm {
  domain: string;
  path: string;
  composeFile: string;
  site: string;
  source: string;
  branch: string;
}

const EMPTY: AdoptForm = { domain: "", path: "", composeFile: "", site: "", source: "", branch: "" };

type AdoptErrors = Partial<Record<keyof AdoptForm, string>>;

/** The form's own checks, before anything is asked: the two fields there is no finding. */
export function adoptProblems(form: AdoptForm, t: (key: "newApp.adopt.domainRequired" | "newApp.adopt.pathRequired" | "newApp.adopt.pathAbsolute") => string): AdoptErrors {
  const errors: AdoptErrors = {};
  if (form.domain.trim() === "") errors.domain = t("newApp.adopt.domainRequired");
  if (form.path.trim() === "") errors.path = t("newApp.adopt.pathRequired");
  else if (!form.path.trim().startsWith("/")) errors.path = t("newApp.adopt.pathAbsolute");
  return errors;
}

/** The request, with what was left empty left out: found by looking. */
export function adoptBody(form: AdoptForm, extra: { preview: boolean; acceptRecreate: boolean }) {
  const optional = (value: string): string | undefined => (value.trim() === "" ? undefined : value.trim());
  const composeFile = optional(form.composeFile);
  const site = optional(form.site);
  const source = optional(form.source);
  const branch = optional(form.branch);
  return {
    domain: normalizeDomain(form.domain),
    path: form.path.trim(),
    preview: extra.preview,
    accept_recreate: extra.acceptRecreate,
    ...(composeFile !== undefined ? { compose_file: composeFile } : {}),
    ...(site !== undefined ? { site } : {}),
    ...(source !== undefined ? { source } : {}),
    ...(branch !== undefined ? { branch } : {}),
  };
}

/** Compose's rehearsal would recreate something: 409 with its output. */
function recreateRefusal(error: unknown): { detail: string; hint: string | null; output: string } | null {
  if (!isApiError(error) || error.status !== 409 || error.output === null) return null;
  return { detail: error.detail, hint: error.hint, output: error.output };
}

const FIELDS = ["domain", "path", "compose_file", "site", "source", "branch"] as const;

/** What names the stack: a refusal, and the choice to recreate anyway, answer for these. */
function sameStack(a: AdoptForm, b: AdoptForm): boolean {
  return a.domain === b.domain && a.path === b.path && a.composeFile === b.composeFile;
}

interface AdoptRequest {
  form: AdoptForm;
  acceptRecreate: boolean;
}

function Found({ preview, t }: { preview: Adoption; t: T }) {
  const projectFrom =
    preview.project_from === "containers"
      ? t("newApp.adopt.projectFromContainers")
      : preview.project_from === "compose"
        ? t("newApp.adopt.projectFromCompose")
        : t("newApp.adopt.projectFromStack");
  const prose = { mono: false, copy: false } as const;
  const items: KeyValueItem[] = [
    { label: t("newApp.adopt.project"), value: preview.project ?? null, hint: projectFrom },
    { label: t("newApp.adopt.composeFile"), value: preview.compose_file },
    {
      label: t("newApp.adopt.containers"),
      value: preview.containers.length > 0 ? preview.containers.join(", ") : t("newApp.adopt.noContainers"),
      ...(preview.containers.length > 0 ? {} : prose),
      hint: preview.running ? t("newApp.adopt.running") : t("newApp.adopt.notRunning"),
    },
    preview.site !== null
      ? { label: t("newApp.adopt.site"), value: preview.site, hint: preview.site_name ? t("newApp.adopt.siteNamed", { name: preview.site_name }) : t("newApp.adopt.siteOperator") }
      : { label: t("newApp.adopt.site"), value: t("newApp.adopt.noSite"), ...prose },
    preview.headless
      ? { label: t("newApp.adopt.port"), value: t("newApp.adopt.headless"), ...prose }
      : { label: t("newApp.adopt.port"), value: preview.port ?? null },
    { label: t("newApp.adopt.source"), value: preview.source },
    { label: t("newApp.adopt.branch"), value: preview.branch ?? null },
    { label: t("newApp.adopt.commit"), value: preview.commit ?? null },
    { label: t("newApp.adopt.unit"), value: `${preview.unit}.service`, hint: t("newApp.adopt.unitHint") },
  ];
  return (
    <Card title={t("newApp.adopt.foundTitle")} description={t("newApp.adopt.foundDescription")}>
      <div className="flex flex-col gap-4">
        <KeyValueList items={items} />
        {preview.warnings.length > 0 ? (
          <Notice tone="warning" title={t("newApp.adopt.warningsTitle")}>
            <ul className="flex list-disc flex-col gap-1 pl-5">
              {preview.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Notice>
        ) : null}
        {preview.changes.length > 0 ? (
          <Notice tone="warning" title={t("newApp.adopt.changesTitle")}>
            <SystemOutput label={t("newApp.adopt.changesLabel")}>{preview.changes.join("\n")}</SystemOutput>
          </Notice>
        ) : null}
        <div className="flex flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("newApp.adopt.dryRunTitle")}</p>
          <p className="text-12 text-pretty text-fg-muted">{t("newApp.adopt.dryRunDescription")}</p>
          <SystemOutput label={t("newApp.adopt.dryRunLabel")} maxHeight="max-h-64" className="rounded-control border border-border bg-bg-sunken p-3">
            {preview.dry_run === "" ? t("newApp.adopt.dryRunEmpty") : preview.dry_run}
          </SystemOutput>
        </div>
      </div>
    </Card>
  );
}

export interface AdoptionState {
  /** The step's content, in the source step's place. */
  content: ReactNode;
  /** The wizard bar's way forward: Preview, then Adopt. */
  actions: WizardActionsProps;
}

/** The adoption's form and its two presses, for the new-app wizard's source step. */
export function useAdoption(): AdoptionState {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<AdoptForm>(EMPTY);
  const [errors, setErrors] = useState<AdoptErrors>({});
  const [acceptRecreate, setAcceptRecreate] = useState(false);
  // The preview answers for the form (and the choice) as it was sent; editing either asks again.
  const [previewed, setPreviewed] = useState<{ for: AdoptRequest; answer: Adoption } | null>(null);
  // Compose's refusal, kept for the stack it answered rather than read from the mutation:
  // ticking "Adopt anyway" must not take away the output it asks the operator to have read,
  // nor the box itself with the focus on it.
  const [refused, setRefused] = useState<{ for: AdoptForm; error: unknown } | null>(null);

  const keepRefusal = (error: unknown, sent: AdoptRequest): void => {
    if (recreateRefusal(error) !== null) setRefused({ for: sent.form, error });
  };
  const preview = useMutation({
    mutationFn: (sent: AdoptRequest) => request("post", "/api/apps/adopt", { body: adoptBody(sent.form, { preview: true, acceptRecreate: sent.acceptRecreate }) }),
    onSuccess: (answer, sent) => {
      setPreviewed({ for: sent, answer });
      announce(t("newApp.adopt.previewReady"));
    },
    onError: keepRefusal,
  });
  const adopt = useMutation({
    mutationFn: (sent: AdoptRequest) => request("post", "/api/apps/adopt", { body: adoptBody(sent.form, { preview: false, acceptRecreate: sent.acceptRecreate }) }),
    onError: keepRefusal,
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      announce(t("newApp.adopt.adopted", { domain: answer.domain }));
      void navigate({ to: "/apps/$domain", params: { domain: answer.domain } });
    },
  });

  const failure = preview.error ?? adopt.error;
  const refusal = refused !== null && sameStack(refused.for, form) ? refused : null;
  const split = splitErrors(recreateRefusal(failure) === null ? failure : null, FIELDS);
  const ready = previewed !== null && previewed.for.acceptRecreate === acceptRecreate && JSON.stringify(previewed.for.form) === JSON.stringify(form);

  const edit = (patch: Partial<AdoptForm>): void => {
    const next = { ...form, ...patch };
    setForm(next);
    setErrors({});
    if (!sameStack(form, next)) {
      // Another stack: what Compose said, and what the operator accepted, were about the last.
      setRefused(null);
      setAcceptRecreate(false);
    }
    if (preview.isError) preview.reset();
    if (adopt.isError) adopt.reset();
  };

  const go = (): void => {
    const found = adoptProblems(form, t);
    if (Object.keys(found).length > 0) {
      setErrors(found);
      return;
    }
    if (ready) adopt.mutate({ form, acceptRecreate });
    else preview.mutate({ form, acceptRecreate });
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    go();
  };

  const fieldError = (key: keyof AdoptForm, api: (typeof FIELDS)[number]): string | undefined => errors[key] ?? split.fields[api];
  const optionalChanged = form.composeFile !== "" || form.site !== "" || form.source !== "" || form.branch !== "";
  const shownDomain = form.domain.trim() === "" ? "example.com" : normalizeDomain(form.domain);
  const shownPath = form.path.trim() === "" ? "/srv/stack" : form.path.trim();

  const content = (
    <div className="flex flex-col gap-5">
      <form id="adopt-stack" onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("newApp.adopt.domain")} error={fieldError("domain", "domain")} description={t("newApp.adopt.domainDescription")}>
          <Input mono value={form.domain} onValueChange={(value: string) => edit({ domain: value })} placeholder="shop.example.com" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("newApp.adopt.path")} error={fieldError("path", "path")} description={t("newApp.adopt.pathDescription")}>
          <Input mono value={form.path} onValueChange={(value: string) => edit({ path: value })} placeholder="/srv/shop" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Disclosure label={t("newApp.adopt.moreOptions")} defaultOpen={optionalChanged}>
          <div className="flex flex-col gap-5">
            <Field label={t("newApp.adopt.composeFileField")} optional error={fieldError("composeFile", "compose_file")} description={t("newApp.adopt.composeFileDescription")}>
              <Input mono value={form.composeFile} onValueChange={(value: string) => edit({ composeFile: value })} placeholder="docker-compose.yml" autoComplete="off" spellCheck={false} />
            </Field>
            <Field label={t("newApp.adopt.siteField")} optional error={fieldError("site", "site")} description={t("newApp.adopt.siteDescription")}>
              <Input mono value={form.site} onValueChange={(value: string) => edit({ site: value })} autoComplete="off" spellCheck={false} />
            </Field>
            <Field label={t("newApp.adopt.sourceField")} optional error={fieldError("source", "source")} description={t("newApp.adopt.sourceDescription")}>
              <Input mono value={form.source} onValueChange={(value: string) => edit({ source: value })} autoComplete="off" spellCheck={false} />
            </Field>
            <Field label={t("newApp.adopt.branchField")} optional error={fieldError("branch", "branch")} description={t("newApp.adopt.branchDescription")}>
              <Input mono value={form.branch} onValueChange={(value: string) => edit({ branch: value })} autoComplete="off" spellCheck={false} />
            </Field>
          </div>
        </Disclosure>
      </form>

      {refusal !== null ? (
        <div className="flex flex-col gap-3">
          <ErrorBlock live compact error={refusal.error} title={t("newApp.adopt.wouldRecreateTitle")} />
          <Checkbox
            label={t("newApp.adopt.acceptRecreate")}
            description={t("newApp.adopt.acceptRecreateDescription")}
            checked={acceptRecreate}
            onCheckedChange={(checked) => {
              setAcceptRecreate(checked);
              preview.reset();
              adopt.reset();
            }}
          />
        </div>
      ) : failure !== null && split.form !== null ? (
        <ErrorBlock live compact error={split.form} title={adopt.isError ? t("newApp.adopt.notAdopted") : t("newApp.adopt.notPreviewed")} />
      ) : null}

      {ready ? <Found preview={previewed.answer} t={t} /> : null}

      <CommandHint command={`noust app adopt ${shownDomain} --path ${shownPath}`} label={t("newApp.source.terminal")} />
    </div>
  );

  return {
    content,
    actions: {
      next: {
        label: ready ? t("newApp.adopt.adopt") : t("newApp.adopt.preview"),
        loading: preview.isPending || adopt.isPending,
        onClick: go,
      },
    },
  };
}
