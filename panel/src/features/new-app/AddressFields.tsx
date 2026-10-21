import { ErrorBlock } from "../../components/page/QueryState";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { CopyValue } from "../../components/ui/CopyValue";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { useT } from "../../i18n";
import { DnsVerdict } from "../domains/DnsVerdict";
import { normalizeDomain } from "../domains/names";
import type { DomainDnsCheck } from "./useDomainDnsCheck";
import { canIncludeWww, withAddressMode } from "./wizard";
import type { AddressChoice, AddressMode } from "./wizard";

function DnsSkeleton() {
  const t = useT();
  return (
    <LoadingRegion label={t("newApp.review.checkingDns")} className="flex items-center gap-2">
      <Skeleton className="size-4" />
      <Skeleton className="h-4 w-56" />
    </LoadingRegion>
  );
}

/**
 * What the address fields read and write. A start that has no temporary address to offer (an
 * export, which brings its own) leaves the last three out and is only the domain.
 */
export interface AddressValue {
  domain: string;
  includeWww: boolean;
  ssl: boolean;
  addressMode?: AddressMode;
  temporaryDomain?: string;
  ownDomain?: string;
}

export interface AddressFieldsProps {
  value: AddressValue;
  /** What changed. Switching between the two addresses changes all of them at once. */
  onChange: (patch: Partial<AddressChoice>) => void;
  error: string | undefined;
  /** Where the typed domain points, from `useDomainDnsCheck`, owned by the step so it can check on submit. */
  dns: DomainDnsCheck;
  /** What the domain field says under it; the review's own sentence when omitted. */
  description?: string;
  /** Offer www and HTTPS; an import takes both from its export. */
  options?: boolean;
}

/** A domain of the operator's own: typed, where it points right now (a warning and never a block), www and HTTPS. */
function OwnDomain({
  value,
  onChange,
  error,
  dns,
  description,
  options,
}: Omit<AddressFieldsProps, "options" | "description"> & { description?: string | undefined; options: boolean }) {
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

/**
 * Where a new application answers. On a server that can make one, two ways: a temporary address
 * made of the server's own (something to open before any DNS) and a domain of the operator's. On a
 * server that cannot, or for an import, the domain alone. One implementation for every way of
 * starting an application.
 */
export function AddressFields({ value, onChange, error, dns, description, options = true }: AddressFieldsProps) {
  const t = useT();
  const mode = value.addressMode;
  const temporary = value.temporaryDomain ?? "";
  const own = <OwnDomain value={value} onChange={onChange} error={error} dns={dns} options={options} {...(description !== undefined ? { description } : {})} />;
  if (mode === undefined || temporary === "") return own;
  return (
    <div className="flex flex-col gap-5">
      <ChoiceCards<AddressMode>
        legend={t("newApp.address.legend")}
        options={[
          { value: "temporary", label: t("newApp.address.temporary"), description: t("newApp.address.temporaryDescription") },
          { value: "own", label: t("newApp.address.own"), description: t("newApp.address.ownDescription") },
        ]}
        value={mode}
        onValueChange={(next) => {
          const { domain, addressMode, temporaryDomain, ownDomain, includeWww, ssl } = withAddressMode(value as AddressChoice, next);
          onChange({ domain, addressMode, temporaryDomain, ownDomain, includeWww, ssl });
        }}
        error={mode === "temporary" ? error : undefined}
      />
      {mode === "temporary" ? (
        <div className="flex flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("newApp.address.willAnswer")}</p>
          <CopyValue value={`https://${temporary}`} label={t("newApp.address.copy")} name={t("newApp.address.willAnswer")} />
          <InlineMessage tone="info">{t("newApp.address.temporaryHelp")}</InlineMessage>
        </div>
      ) : (
        own
      )}
    </div>
  );
}
