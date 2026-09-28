import { ErrorBlock } from "../../components/page/QueryState";
import { Checkbox } from "../../components/ui/Checkbox";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { DnsVerdict } from "../domains/DnsVerdict";
import { normalizeDomain } from "../domains/names";
import type { DomainDnsCheck } from "./useDomainDnsCheck";
import { canIncludeWww } from "./wizard";

function DnsSkeleton() {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-3 rounded-card border border-border p-3">
      <span className="sr-only">{t("newApp.review.checkingDns")}</span>
      <Skeleton className="h-4 w-56" />
      <div className="grid gap-3 sm:grid-cols-2">
        <Skeleton className="h-10" />
        <Skeleton className="h-10" />
      </div>
    </div>
  );
}

export interface AddressValue {
  domain: string;
  includeWww: boolean;
  ssl: boolean;
}

export interface AddressFieldsProps {
  value: AddressValue;
  onChange: (patch: Partial<AddressValue>) => void;
  error: string | undefined;
  /** Where the typed domain points, from `useDomainDnsCheck`, owned by the step so it can check on submit. */
  dns: DomainDnsCheck;
  /** What the domain field says under it; the review's own sentence when omitted. */
  description?: string;
  /** Offer www and HTTPS; an import takes both from its export. */
  options?: boolean;
}

/**
 * Where a new application answers: the domain, where it points right now (checked as it is
 * typed, a warning and never a block), and whether it also answers on www and over HTTPS.
 * One implementation for every way of starting an application.
 */
export function AddressFields({ value, onChange, error, dns, description, options = true }: AddressFieldsProps) {
  const t = useT();
  const domain = normalizeDomain(value.domain);
  return (
    <div className="flex flex-col gap-5">
      <Field label={t("newApp.review.domain")} error={error} description={description ?? t("newApp.review.domainDescription")}>
        <Input
          mono
          value={value.domain}
          onValueChange={(next: string) => onChange({ domain: next })}
          placeholder="app.example.com"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          inputMode="url"
        />
      </Field>
      <div aria-live="polite" className="flex flex-col gap-2">
        {dns.data ? (
          <DnsVerdict check={dns.data} />
        ) : dns.isError ? (
          <ErrorBlock
            compact
            error={dns.error}
            title={domain ? t("newApp.review.dnsFailed", { domain }) : t("newApp.review.dnsFailedNoDomain")}
          />
        ) : dns.isFetching ? (
          <DnsSkeleton />
        ) : null}
      </div>
      {options && canIncludeWww(value.domain) ? (
        <Checkbox
          label={t("newApp.review.www")}
          description={t("newApp.review.wwwDescription", { domain })}
          checked={value.includeWww}
          onCheckedChange={(includeWww) => onChange({ includeWww })}
        />
      ) : null}
      {options ? (
        <Checkbox
          label={t("newApp.review.https")}
          description={domain ? t("newApp.review.httpsDescription", { domain }) : t("newApp.review.httpsDescriptionNoDomain")}
          checked={value.ssl}
          onCheckedChange={(ssl) => onChange({ ssl })}
        />
      ) : null}
    </div>
  );
}
