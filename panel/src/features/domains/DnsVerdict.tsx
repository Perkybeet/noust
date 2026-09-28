import { Check, TriangleAlert, X } from "lucide-react";
import type { ReactNode } from "react";

import type { DnsCheck } from "../../api/queries/domains";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { dnsVerdict, isPrivateAddress, recordsToCreate } from "./dns";

function AddressList({ label, addresses, matches, empty }: { label: string; addresses: readonly string[]; matches?: ReadonlySet<string>; empty: string }) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <p className="text-12 text-fg-muted">{label}</p>
      {addresses.length === 0 ? (
        <p className="text-13 text-fg-faint">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {addresses.map((address) => {
            const matched = matches?.has(address);
            return (
              <li key={address} className="flex min-w-0 items-center gap-1.5">
                {matches === undefined ? null : matched ? (
                  <Check aria-hidden="true" className="size-3.5 shrink-0 text-ok" />
                ) : (
                  <X aria-hidden="true" className="size-3.5 shrink-0 text-fail" />
                )}
                <code translate="no" className="truncate text-12 text-fg">
                  {address}
                </code>
                {matches === undefined ? null : (
                  <span className="sr-only">{matched ? t("domains.dnsVerdict.thisServerSr") : t("domains.dnsVerdict.notThisServerSr")}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * The answer to "does this name point here?": a verdict in words and shape, this server's
 * addresses beside the ones the name resolves to, and the records to create when it does not.
 */
export function DnsVerdict({ check, className }: { check: DnsCheck; className?: string }) {
  const t = useT();
  const verdict = dnsVerdict(check);
  const expected = new Set(check.expected_addresses);
  const records = recordsToCreate(check.expected_addresses);
  const onlyPrivate = check.expected_addresses.length > 0 && check.expected_addresses.every(isPrivateAddress);
  const heading =
    verdict === "here"
      ? t("domains.dnsVerdict.pointsHere", { domain: check.domain })
      : verdict === "missing"
        ? t("domains.dnsVerdict.hasNoRecord", { domain: check.domain })
        : t("domains.dnsVerdict.pointsElsewhere", { domain: check.domain });

  const recordList: ReactNode = (
    <>
      {records.map((record, index) => (
        <span key={record.value}>
          {index > 0 ? (index === records.length - 1 ? ` ${t("domains.dnsVerdict.and")} ` : ", ") : null}
          {t.rich("domains.dnsVerdict.recordPhrase", {
            type: record.type,
            value: (
              <code translate="no" className="text-12 text-fg">
                {record.value}
              </code>
            ),
          })}
        </span>
      ))}
    </>
  );

  return (
    <div
      data-verdict={verdict}
      className={cx(
        "flex min-w-0 flex-col gap-3 rounded-card border p-3",
        verdict === "here" ? "border-ok/30 bg-ok-soft/40" : "border-warn/30 bg-warn-soft/40",
        className,
      )}
    >
      <p className="flex items-center gap-2 text-14 font-medium text-fg">
        {verdict === "here" ? (
          <StatusGlyph state="running" className="text-ok" />
        ) : (
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0 text-warn" />
        )}
        <span className="min-w-0 break-words">{heading}</span>
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <AddressList label={t("domains.dnsVerdict.thisServerLabel")} addresses={check.expected_addresses} empty={t("domains.dnsVerdict.noPublicAddress")} />
        <AddressList
          label={t("domains.dnsVerdict.resolvesToLabel", { domain: check.domain })}
          addresses={check.resolved_addresses}
          matches={expected}
          empty={t("domains.dnsVerdict.nothingNoRecord")}
        />
      </div>
      {verdict !== "here" ? (
        <p className="text-13 text-pretty text-fg-muted">
          {records.length > 0
            ? t.rich("domains.dnsVerdict.pointHere", { domain: check.domain, records: recordList })
            : t("domains.dnsVerdict.dnsChangesTakeAWhile")}
          {onlyPrivate ? ` ${t("domains.dnsVerdict.onlyPrivateNote")}` : null}
        </p>
      ) : null}
    </div>
  );
}
