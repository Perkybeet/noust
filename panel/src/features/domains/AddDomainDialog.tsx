import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { nameDnsCheckQuery } from "../../api/queries/domains";
import type { AppDomainChange } from "../../api/queries/domains";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import type { CodedText } from "../../lib/serverSentence";
import { DnsVerdict } from "./DnsVerdict";
import { dnsVerdict, reachesServer } from "./dns";
import { domainProblem, isWildcard, normalizeDomain } from "./names";
import { SiteFields, useSiteTemplate } from "./SiteFields";
import { createSiteBody } from "./siteForm";
import type { CreateSiteBody, CreateSiteForm } from "./siteForm";

/** What a name is for: an application answers on it, it sends visitors to an application, or it is a site of its own. */
export type NamePurpose = "app" | "redirect" | "site";

type AddableKind = "alias" | "redirect";

function kinds(t: T): readonly { value: AddableKind; label: string; description: (app: string) => string }[] {
  return [
    { value: "alias", label: t("domains.kindAlias"), description: (app) => t("domains.addDomainDialog.aliasDescription", { app }) },
    { value: "redirect", label: t("domains.kindRedirect"), description: (app) => t("domains.addDomainDialog.redirectDescription", { app }) },
  ];
}

function KindChoice({ app, value, onChange }: { app: string; value: AddableKind; onChange: (kind: AddableKind) => void }) {
  const t = useT();
  return (
    <ChoiceCards
      legend={t("domains.addDomainDialog.roleLegend")}
      options={kinds(t).map((kind) => ({ value: kind.value, label: kind.label, description: kind.description(app) }))}
      value={value}
      onValueChange={onChange}
    />
  );
}

function VerdictSkeleton() {
  const t = useT();
  return (
    <LoadingRegion label={t("domains.checkingDns")} className="flex flex-col gap-3 rounded-card border border-border p-3">
      <Skeleton className="h-4 w-56" />
      <div className="grid gap-3 sm:grid-cols-2">
        <Skeleton className="h-10" />
        <Skeleton className="h-10" />
      </div>
    </LoadingRegion>
  );
}

/** What making a site answered: its name and the server's own account of it, with its code. */
export interface SiteMade {
  site: string;
  message: string;
  coded?: CodedText | null | undefined;
}

export interface AddDomainDialogProps {
  /** The application's primary domain; null when the dialog asks what the name is for and which application (the Domains area's). */
  app: string | null;
  /** The applications to choose from, when `app` is null. */
  apps?: readonly string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The name was put on an application: its domains afterwards, the name added and the application it went to. */
  onAdded: (change: AppDomainChange, name: string, app: string) => void;
  /** What the dialog starts as: a site of its own from the Sites list, an application's name from anywhere else. */
  initialPurpose?: NamePurpose;
  /** The web server this machine runs, which a site of its own uses unless told otherwise. */
  webserver?: string;
  /** A site of its own was written. Without it the area has no sites to offer, and the dialog is for applications' names. */
  onSiteMade?: (made: SiteMade) => void;
}

/**
 * The one way to add a name (owner item 119: "Add a domain" and "Create a site" were two dialogs): the name, what it is
 * for (an application, a redirect to one, a site of its own), where it points and what the DNS provider needs. DNS is asked
 * where the name points as soon as the operator leaves the field or presses Check, and the verdict is on screen when they
 * reach the button, so adding is one press. Pressed before the answer is in, the button waits for it and goes on if the name
 * points here; if it does not, the verdict is shown and the button says "Add anyway". A name that does not point here can
 * still be added; the dialog says what that costs (the certificate order fails until it does).
 *
 * From an application's own Domains section the application is given: it is a name for that application, and the choice
 * of what it is for is one of its options.
 */
export function AddDomainDialog({ app: given, apps = [], open, onOpenChange, onAdded, initialPurpose = "app", webserver = "nginx", onSiteMade }: AddDomainDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const mayMakeSites = given === null && onSiteMade !== undefined;
  // The only application to choose from is the one: nothing to ask.
  const [chosen, setChosen] = useState<string | null>(null);
  const picked = given ?? chosen ?? (apps.length === 1 ? (apps[0] ?? null) : null);
  // The hooks below need a string; a submit without an application is stopped before it reaches the server.
  const app = picked ?? "";
  const [purpose, setPurpose] = useState<NamePurpose>(initialPurpose === "site" && !mayMakeSites ? "app" : initialPurpose);
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<AddableKind>("alias");
  const [checked, setChecked] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [port, setPort] = useState("");
  const [pointDns, setPointDns] = useState(true);
  const [site, setSite] = useState<CreateSiteForm>({ domain: "", webserver: webserver === "apache" ? "apache" : "nginx", template: "proxy", port: "3000", ssl: true, enable: true });
  const [siteProblems, setSiteProblems] = useState<{ port?: string }>({});

  // What it is for decides what is added: an alias of an application, a redirect to it, or a site of its own.
  const asSite = purpose === "site";
  const addKind: AddableKind = given !== null ? kind : purpose === "redirect" ? "redirect" : "alias";
  const siteTemplate = useSiteTemplate(site, open && asSite);

  // From an application the check is hers; from the area it is the bare check, which needs no application yet.
  const dns = useQuery({ ...nameDnsCheckQuery(given, checked ?? ""), enabled: open && checked !== null && checked !== "" });
  const add = useMutation({
    mutationFn: ({ domain, force }: { domain: string; force: boolean }) =>
      request("post", "/api/apps/{domain}/domains", {
        params: { domain: app },
        body: {
          domain,
          kind: addKind,
          force,
          dns: pointDns,
          ...(addKind === "alias" && port.trim() !== "" ? { port: Number(port) } : {}),
        },
      }),
    onSuccess: (change, { domain }) => {
      onAdded(change, domain, app);
      close(false, true);
    },
  });
  const createSite = useMutation({
    mutationFn: (body: CreateSiteBody) => request("post", "/api/sites", { body }),
    onSuccess: (result) => {
      onSiteMade?.(result);
      close(false, true);
    },
  });
  const working = add.isPending || createSite.isPending;

  // A server with no domain yet: the name made of its public address, which sslip.io answers.
  // A server behind NAT answers that it has no such name, with how to declare its address.
  const [noAddress, setNoAddress] = useState<{ reason: string; hint: string | null } | null>(null);
  const temporary = useMutation({
    mutationFn: () => request("get", "/api/domains/temporary", {}),
    onSuccess: (found) => {
      if (found.available === false) {
        setNoAddress({ reason: found.reason ?? "", hint: found.hint ?? null });
        return;
      }
      setNoAddress(null);
      setName(found.domain);
      setProblem(null);
      setChecked(null);
    },
  });

  const normalized = normalizeDomain(name);
  const wildcard = !asSite && isWildcard(normalized);
  // A wildcard has no address to ask DNS about: its certificate is proven with a DNS record.
  const isChecked = wildcard || (checked !== null && checked === normalized);
  const verdict = isChecked && dns.data ? dnsVerdict(dns.data) : null;
  // Another site of the web server answers on the name: the operator decides, with its words in front.
  const refusal = add.error ?? createSite.error;
  const overlap = refusal && isApiError(refusal) && refusal.error === "domain_overlap" ? refusal : null;

  // `settled` closes it from the mutation's own success callback, which runs while the
  // mutation still reads as pending.
  function close(next: boolean, settled = false): void {
    if (!next && working && !settled) return;
    onOpenChange(next);
    if (!next) {
      setName("");
      setKind("alias");
      setPurpose(initialPurpose === "site" && !mayMakeSites ? "app" : initialPurpose);
      setChecked(null);
      setProblem(null);
      setPort("");
      setPointDns(true);
      setAsking(false);
      setChosen(null);
      setSiteProblems({});
      add.reset();
      createSite.reset();
      temporary.reset();
      setNoAddress(null);
    }
  }

  /** Why the name cannot be added at all, or null: the same words on leaving the field and on pressing Add. */
  const problemWith = (): string | null => {
    if (!asSite && picked === null) return t("domains.addDomainDialog.appNeeded");
    const invalid = domainProblem(name, t.locale, { wildcard: !asSite });
    if (invalid !== null) return invalid;
    if (!asSite && normalized === app) return t("domains.addDomainDialog.alreadyPrimaryError", { app });
    if (!asSite && port.trim() !== "" && (!/^\d+$/.test(port.trim()) || Number(port) < 1 || Number(port) > 65535)) return t("network.port.invalid");
    return null;
  };

  /** Asks DNS about the name now: leaving the field with a name that could be added, or pressing Check. */
  const askDns = (): void => {
    if (name.trim() === "" || domainProblem(name, t.locale, { wildcard: !asSite }) !== null || (!asSite && normalized === app) || wildcard) return;
    if (given !== null && picked === null) return;
    setChecked(normalized);
  };

  const proceed = (force: boolean): void => {
    if (asSite) {
      const made = createSiteBody({ ...site, domain: name, template: siteTemplate.template }, t.locale);
      if ("errors" in made) {
        setSiteProblems(made.errors.port !== undefined ? { port: made.errors.port } : {});
        if (made.errors.domain !== undefined) setProblem(made.errors.domain);
        return;
      }
      createSite.mutate(made.body);
      return;
    }
    add.mutate({ domain: normalized, force });
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const invalid = problemWith();
    if (invalid !== null) {
      setProblem(invalid);
      return;
    }
    setProblem(null);
    if (wildcard || (isChecked && !dns.isPending)) {
      proceed(overlap !== null);
      return;
    }
    // The answer is not in yet (the button was pressed straight from the field): wait for the one
    // already asked, and go on only if the name points here. Otherwise the verdict is shown.
    setChecked(normalized);
    setAsking(true);
    void queryClient
      .query({ ...nameDnsCheckQuery(given, normalized), staleTime: "static" })
      .then((check) => {
        const answer = dnsVerdict(check);
        if (reachesServer(answer) || answer === "unknown") proceed(false);
      })
      .catch(() => undefined)
      .finally(() => setAsking(false));
  };

  const serverFields = refusal && isApiError(refusal) ? (refusal.fields ?? {}) : {};
  const fieldError = problem ?? serverFields["domain"] ?? null;
  const primaryLabel =
    overlap !== null
      ? t("network.addDomain.addAnyway")
      : verdict === null || reachesServer(verdict) || verdict === "unknown"
        ? t("domains.addDomainDialog.addName")
        : t("domains.addDomainDialog.addAnyway");
  const who = picked ?? t("domains.addDomainDialog.theApplication");

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={given !== null ? t("domains.addDomainDialog.addDialogTitle", { app: given }) : t("domains.addDomainDialog.addDialogTitleAny")}
      description={t("domains.addDomainDialog.addDialogDescription")}
      footer={
        <>
          <Button disabled={working} onClick={() => close(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={working || asking}>
            {primaryLabel}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("common.words.name")} error={fieldError} description={t("domains.addDomainDialog.domainFieldDescription")}>
          <Input
            mono
            value={name}
            onValueChange={(value: string) => {
              setName(value);
              setProblem(null);
            }}
            placeholder={given !== null ? `shop.${given}` : "shop.example.com"}
            onBlur={askDns}
            suffix={
              <Button size="sm" variant="ghost" loading={dns.isFetching && isChecked} onClick={askDns}>
                {t("domains.addDomainDialog.check")}
              </Button>
            }
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            inputMode="url"
          />
        </Field>
        <div className="flex flex-col gap-1.5">
          <div>
            <Button size="sm" variant="ghost" loading={temporary.isPending} onClick={() => temporary.mutate()}>
              {t("domains.temporary.use")}
            </Button>
          </div>
          {noAddress !== null ? (
            <ErrorBlock compact error={{ detail: noAddress.reason }} title={t("domains.temporary.unavailable")} {...(noAddress.hint !== null ? { hint: noAddress.hint } : {})} />
          ) : temporary.isError ? (
            <ErrorBlock compact error={temporary.error} title={t("domains.temporary.unavailable")} />
          ) : (
            <p className="text-12 text-pretty text-fg-muted">{t("domains.temporary.help")}</p>
          )}
        </div>

        {given === null ? (
          <>
            {mayMakeSites ? (
              <ChoiceCards<NamePurpose>
                legend={t("domains.addDomainDialog.purposeLegend")}
                columns={1}
                value={purpose}
                onValueChange={(next) => {
                  setPurpose(next);
                  setProblem(null);
                  setChecked(null);
                }}
                options={[
                  { value: "app", label: t("domains.addDomainDialog.purposeApp"), description: t("domains.addDomainDialog.purposeAppHelp") },
                  { value: "redirect", label: t("domains.addDomainDialog.purposeRedirect"), description: t("domains.addDomainDialog.purposeRedirectHelp") },
                  { value: "site", label: t("domains.addDomainDialog.purposeSite"), description: t("domains.addDomainDialog.purposeSiteHelp") },
                ]}
              />
            ) : (
              <ChoiceCards<AddableKind>
                legend={t("domains.addDomainDialog.purposeLegend")}
                columns={1}
                value={purpose === "redirect" ? "redirect" : "alias"}
                onValueChange={(next) => setPurpose(next === "redirect" ? "redirect" : "app")}
                options={[
                  { value: "alias", label: t("domains.addDomainDialog.purposeApp"), description: t("domains.addDomainDialog.purposeAppHelp") },
                  { value: "redirect", label: t("domains.addDomainDialog.purposeRedirect"), description: t("domains.addDomainDialog.purposeRedirectHelp") },
                ]}
              />
            )}
            {!asSite && apps.length !== 1 ? (
              <Field
                label={purpose === "redirect" ? t("domains.addDomainDialog.redirectsTo") : t("domains.addDomainDialog.appLabel")}
                nativeLabel={false}
                error={problem === t("domains.addDomainDialog.appNeeded") ? problem : null}
              >
                <Select
                  aria-label={purpose === "redirect" ? t("domains.addDomainDialog.redirectsTo") : t("domains.addDomainDialog.appLabel")}
                  value={chosen}
                  placeholder={t("domains.addDomainDialog.appPlaceholder")}
                  onValueChange={(value: string) => {
                    setChosen(value);
                    setProblem(null);
                  }}
                  options={apps.map((entry) => ({ value: entry, label: entry }))}
                />
              </Field>
            ) : null}
          </>
        ) : null}

        {asSite ? (
          <SiteFields
            form={site}
            errors={siteProblems}
            server={serverFields}
            onChange={(patch) => {
              setSite((current) => ({ ...current, ...patch }));
              setSiteProblems({});
            }}
            detected={webserver}
            template={siteTemplate.template}
            available={siteTemplate.available}
            templates={siteTemplate.query}
          />
        ) : (
          // What most names never change: an alias, on the application's own port, with its DNS record made.
          <Disclosure label={t("domains.addDomainDialog.moreOptions")} defaultOpen={false}>
            <div className="flex flex-col gap-5">
              {given !== null ? <KindChoice app={who} value={kind} onChange={setKind} /> : null}
              {addKind === "alias" ? (
                <Field label={t("network.addDomain.portLabel")} description={t("network.addDomain.portHelp", { app: who })} optional>
                  <Input mono inputMode="numeric" value={port} onValueChange={setPort} autoComplete="off" />
                </Field>
              ) : null}
              <Checkbox label={t("network.addDomain.dnsLabel")} description={t("network.addDomain.dnsHelp")} checked={pointDns} onCheckedChange={setPointDns} />
            </div>
          </Disclosure>
        )}
        {wildcard ? <Notice title={t("network.addDomain.wildcardTitle")}>{t("network.addDomain.wildcardNote")}</Notice> : null}

        {isChecked && !wildcard ? (
          <div className="flex flex-col gap-2" aria-live="polite">
            {dns.data ? (
              <DnsVerdict check={dns.data} />
            ) : dns.isError ? (
              <ErrorBlock compact error={dns.error} title={t("domains.couldNotResolve", { name: normalized })} />
            ) : (
              <VerdictSkeleton />
            )}
            {dns.data || dns.isError ? (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-12 text-pretty text-fg-muted">
                  {verdict !== null && reachesServer(verdict) ? t("domains.addDomainDialog.addingExtendsCertHint") : t("domains.addDomainDialog.addedNowHint")}
                </p>
                <Button size="sm" variant="ghost" icon={<RotateCw aria-hidden="true" />} loading={dns.isFetching} onClick={() => void dns.refetch()}>
                  {t("domains.checkAgain")}
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        {overlap !== null ? (
          <Notice tone="warning" live title={t("network.addDomain.overlapTitle", { name: normalized })}>
            <span className="block">{overlap.detail}</span>
            {overlap.hint ? <span className="mt-1 block text-fg-muted">{overlap.hint}</span> : null}
          </Notice>
        ) : null}
        {refusal && fieldError === null && overlap === null && Object.keys(serverFields).length === 0 ? (
          <ErrorBlock
            live
            compact
            error={refusal}
            title={asSite ? t("domains.createSiteDialog.notCreatedError", { domain: normalized || t("domains.theSite") }) : t("domains.addDomainDialog.notAddedError", { name: normalized })}
          />
        ) : null}
      </form>
    </Dialog>
  );
}
