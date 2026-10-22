import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { dnsCheckQuery } from "../../api/queries/domains";
import type { AppDomainChange } from "../../api/queries/domains";
import { ErrorBlock } from "../../components/page/QueryState";
import { Checkbox } from "../../components/ui/Checkbox";
import { Notice } from "../../components/ui/Notice";
import { Button } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { DnsVerdict } from "./DnsVerdict";
import { dnsVerdict, reachesServer } from "./dns";
import { domainProblem, isWildcard, normalizeDomain } from "./names";
import { LoadingRegion } from "../../components/page/LoadingRegion";

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

export interface AddDomainDialogProps {
  /** The application's primary domain. */
  app: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The domain was added: the application's domains afterwards, and the name added. */
  onAdded: (change: AppDomainChange, name: string) => void;
}

/**
 * Adds a name to an application. DNS is asked where the name points as soon as the operator
 * leaves the field, and the verdict is on screen when they reach the button, so adding is one
 * press. Pressed before the answer is in, the button waits for it and goes on if the name points
 * here; if it does not, the verdict is shown and the button says "Add anyway". A name that does
 * not point here can still be added; the dialog says what that costs (the certificate order fails
 * until it does).
 */
export function AddDomainDialog({ app, open, onOpenChange, onAdded }: AddDomainDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<AddableKind>("alias");
  const [checked, setChecked] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [port, setPort] = useState("");
  const [pointDns, setPointDns] = useState(true);

  const dns = useQuery({ ...dnsCheckQuery(app, checked ?? ""), enabled: open && checked !== null });
  const add = useMutation({
    mutationFn: ({ domain, force }: { domain: string; force: boolean }) =>
      request("post", "/api/apps/{domain}/domains", {
        params: { domain: app },
        body: {
          domain,
          kind,
          force,
          dns: pointDns,
          ...(kind === "alias" && port.trim() !== "" ? { port: Number(port) } : {}),
        },
      }),
    onSuccess: (change, { domain }) => {
      onAdded(change, domain);
      close(false, true);
    },
  });

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
  const wildcard = isWildcard(normalized);
  // A wildcard has no address to ask DNS about: its certificate is proven with a DNS record.
  const isChecked = wildcard || (checked !== null && checked === normalized);
  const verdict = isChecked && dns.data ? dnsVerdict(dns.data) : null;
  // Another site of the web server answers on the name: the operator decides, with its words in front.
  const overlap = add.error && isApiError(add.error) && add.error.error === "domain_overlap" ? add.error : null;

  // `settled` closes it from the mutation's own success callback, which runs while the
  // mutation still reads as pending.
  function close(next: boolean, settled = false): void {
    if (!next && add.isPending && !settled) return;
    onOpenChange(next);
    if (!next) {
      setName("");
      setKind("alias");
      setChecked(null);
      setProblem(null);
      setPort("");
      setPointDns(true);
      setAsking(false);
      add.reset();
      temporary.reset();
      setNoAddress(null);
    }
  }

  /** Why the name cannot be added at all, or null: the same words on leaving the field and on pressing Add. */
  const problemWith = (): string | null => {
    const invalid = domainProblem(name, t.locale, { wildcard: true });
    if (invalid !== null) return invalid;
    if (normalized === app) return t("domains.addDomainDialog.alreadyPrimaryError", { app });
    if (port.trim() !== "" && (!/^\d+$/.test(port.trim()) || Number(port) < 1 || Number(port) > 65535)) return t("network.port.invalid");
    return null;
  };

  /** Leaving the field with a name that could be added asks DNS about it, so the verdict is there before the button is. */
  const askDns = (): void => {
    if (name.trim() === "" || domainProblem(name, t.locale, { wildcard: true }) !== null || normalized === app || wildcard) return;
    setChecked(normalized);
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
      add.mutate({ domain: normalized, force: overlap !== null });
      return;
    }
    // The answer is not in yet (the button was pressed straight from the field): wait for the one
    // already asked, and go on only if the name points here. Otherwise the verdict is shown.
    setChecked(normalized);
    setAsking(true);
    void queryClient
      .query({ ...dnsCheckQuery(app, normalized), staleTime: "static" })
      .then((check) => {
        const kind = dnsVerdict(check);
        if (reachesServer(kind) || kind === "unknown") add.mutate({ domain: normalized, force: false });
      })
      .catch(() => undefined)
      .finally(() => setAsking(false));
  };

  const fieldError = problem ?? (add.error && isApiError(add.error) ? (add.error.fields?.["domain"] ?? null) : null);
  const primaryLabel =
    overlap !== null
      ? t("network.addDomain.addAnyway")
      : verdict === null || reachesServer(verdict) || verdict === "unknown"
        ? t("domains.addDomain")
        : t("domains.addDomainDialog.addAnyway");
  const formId = useId();

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={t("domains.addDomainDialog.addDialogTitle", { app })}
      description={t("domains.addDomainDialog.addDialogDescription")}
      footer={
        <>
          <Button disabled={add.isPending} onClick={() => close(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={add.isPending || asking}
          >
            {primaryLabel}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("domains.domainFieldLabel")} error={fieldError} description={t("domains.addDomainDialog.domainFieldDescription")}>
          <Input
            mono
            value={name}
            onValueChange={(value: string) => {
              setName(value);
              setProblem(null);
            }}
            placeholder={`shop.${app}`}
            onBlur={askDns}
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
        <KindChoice app={app} value={kind} onChange={setKind} />
        {kind === "alias" ? (
          <Field label={t("network.addDomain.portLabel")} description={t("network.addDomain.portHelp", { app })} optional>
            <Input mono inputMode="numeric" value={port} onValueChange={setPort} autoComplete="off" />
          </Field>
        ) : null}
        <Checkbox label={t("network.addDomain.dnsLabel")} description={t("network.addDomain.dnsHelp")} checked={pointDns} onCheckedChange={setPointDns} />
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
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<RotateCw aria-hidden="true" />}
                  loading={dns.isFetching}
                  onClick={() => void dns.refetch()}
                >
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
        {add.isError && fieldError === null && overlap === null ? (
          <ErrorBlock live compact error={add.error} title={t("domains.addDomainDialog.notAddedError", { name: normalized })} />
        ) : null}
      </form>
    </Dialog>
  );
}
