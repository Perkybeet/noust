import { useMutation, useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { dnsCheckQuery } from "../../api/queries/domains";
import type { AppDomainChange } from "../../api/queries/domains";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { DnsVerdict } from "./DnsVerdict";
import { dnsVerdict } from "./dns";
import { domainProblem, normalizeDomain } from "./names";

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
    <div aria-busy="true" className="flex flex-col gap-3 rounded-card border border-border p-3">
      <span className="sr-only">{t("domains.checkingDns")}</span>
      <Skeleton className="h-4 w-56" />
      <div className="grid gap-3 sm:grid-cols-2">
        <Skeleton className="h-10" />
        <Skeleton className="h-10" />
      </div>
    </div>
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
 * Adds a name to an application in two moves with one button: first it asks DNS where the
 * name points and shows the verdict, then it adds the name. A name that does not point here
 * can still be added; the dialog says what that costs (the certificate order fails until it
 * does) before the operator decides.
 */
export function AddDomainDialog({ app, open, onOpenChange, onAdded }: AddDomainDialogProps) {
  const t = useT();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<AddableKind>("alias");
  const [checked, setChecked] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const dns = useQuery({ ...dnsCheckQuery(app, checked ?? ""), enabled: open && checked !== null });
  const add = useMutation({
    mutationFn: (domain: string) =>
      request("post", "/api/apps/{domain}/domains", { params: { domain: app }, body: { domain, kind } }),
    onSuccess: (change, domain) => {
      onAdded(change, domain);
      close(false, true);
    },
  });

  const normalized = normalizeDomain(name);
  const isChecked = checked !== null && checked === normalized;
  const verdict = isChecked && dns.data ? dnsVerdict(dns.data) : null;

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
      add.reset();
    }
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const invalid = domainProblem(name, t.locale);
    if (invalid !== null) {
      setProblem(invalid);
      return;
    }
    if (normalized === app) {
      setProblem(t("domains.addDomainDialog.alreadyPrimaryError", { app }));
      return;
    }
    setProblem(null);
    if (!isChecked) {
      setChecked(normalized);
      return;
    }
    if (dns.isPending) return;
    add.mutate(normalized);
  };

  const fieldError = problem ?? (add.error && isApiError(add.error) ? (add.error.fields?.["domain"] ?? null) : null);
  const primaryLabel = !isChecked ? t("domains.checkDns") : verdict === null || verdict === "here" ? t("domains.addDomain") : t("domains.addDomainDialog.addAnyway");
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
            {t("domains.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            loading={add.isPending || (isChecked && dns.isFetching && dns.data === undefined)}
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
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            inputMode="url"
          />
        </Field>
        <KindChoice app={app} value={kind} onChange={setKind} />

        {isChecked ? (
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
                  {verdict === "here" ? t("domains.addDomainDialog.addingExtendsCertHint") : t("domains.addDomainDialog.addedNowHint")}
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

        {add.isError && fieldError === null ? (
          <ErrorBlock live compact error={add.error} title={t("domains.addDomainDialog.notAddedError", { name: normalized })} />
        ) : null}
      </form>
    </Dialog>
  );
}
