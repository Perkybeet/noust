import { Play } from "lucide-react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDate, parseTimestamp } from "../../lib/format";
import { CertificateStatus } from "../domains/CertificateStatus";
import { certificateView, issuerName } from "../domains/certificates";
import { useFleetActions } from "./BulkActionDialog";
import { field, numberOf, originOf } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { HrefLink } from "./links";
import { PartialNotice, ServerCell, ServerFilter, StateFilter, useFleetView } from "./parts";

const CERT_STATES = ["expired", "expiring", "valid"] as const;

function daysOf(row: FleetRow): number | null {
  return numberOf(row["days_remaining"]);
}

function stateOf(row: FleetRow): (typeof CERT_STATES)[number] | null {
  const days = daysOf(row);
  if (days === null) return null;
  const tone = certificateView({ days_remaining: days }).tone;
  return tone === "fail" ? "expired" : tone === "warn" ? "expiring" : "valid";
}

function names(row: FleetRow): string[] {
  const list = Array.isArray(row["domains"]) ? row["domains"].filter((name): name is string => typeof name === "string") : [];
  const main = field(row, "domain");
  return list.length > 0 ? list : main !== null ? [main] : [];
}

const NAME_LINK = "min-w-0 rounded-chip font-medium text-fg hover:underline hover:underline-offset-2";

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<FleetRow>[] {
  return [
    {
      id: "certificate",
      header: t("fleet.column.certificate"),
      card: "title",
      sortValue: (row) => field(row, "domain"),
      cell: (row) => {
        const all = names(row);
        return (
          <span className="flex min-w-0 flex-col items-start gap-0.5">
            <HrefLink href={originOf(row).href} className={NAME_LINK}>
              <Mono truncate>{field(row, "domain") ?? all[0] ?? ""}</Mono>
            </HrefLink>
            {all.length > 1 ? (
              <Mono tone="muted" truncate className="text-12">
                {all.filter((name) => name !== field(row, "domain")).join(", ")}
              </Mono>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "expiry",
      header: t("fleet.column.expiry"),
      card: "status",
      width: "w-48",
      sortValue: (row) => daysOf(row) ?? Number.POSITIVE_INFINITY,
      cell: (row) => {
        const view = certificateView({ days_remaining: daysOf(row) }, t.locale);
        return <CertificateStatus tone={view.tone} label={view.label} />;
      },
    },
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "meta",
      sortValue: (row) => originOf(row).node,
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
    {
      id: "date",
      header: t("fleet.column.expiresOn"),
      hideBelow: "md",
      card: "meta",
      cell: (row) => {
        const date = parseTimestamp(field(row, "valid_until") ?? field(row, "expires_on"));
        return date === null ? <EmptyCell reason={t("fleet.certificates.noDate")} /> : <span className="text-13 text-fg tabular-nums">{formatDate(date, {}, t.locale)}</span>;
      },
    },
    {
      id: "renewal",
      header: t("fleet.column.renewal"),
      hideBelow: "lg",
      card: "meta",
      cell: (row) => (
        <span className="text-13 text-fg">{row["auto_renew"] === true ? t("fleet.certificates.autoRenew") : t("fleet.certificates.manualRenew")}</span>
      ),
    },
    {
      id: "issuer",
      header: t("fleet.column.issuer"),
      hideBelow: "lg",
      card: "hidden",
      cell: (row) => {
        const issuer = field(row, "issuer");
        return issuer === null ? <EmptyCell reason={t("fleet.certificates.noIssuer")} /> : <span className="text-13 text-fg-muted">{issuerName(issuer)}</span>;
      },
    },
  ];
}

export interface CertificatesTabProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/** Every certificate of every server (T1), the soonest to expire first, each opening on its server. */
export function CertificatesTab({ search, onSearchChange }: CertificatesTabProps) {
  const t = useT();
  const view = useFleetView("certificates");
  const actions = useFleetActions();
  const rows = view.data?.items ?? [];
  const shown = rows.filter((row) => {
    const origin = originOf(row);
    if (search.server !== undefined && origin.node !== search.server) return false;
    if (search.state !== undefined && stateOf(row) !== search.state) return false;
    return matchesQuery([...names(row), origin.node], search.q);
  });

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PartialNotice view={view.data} />
      <FilterBar
        label={t("fleet.certificates.filterLabel")}
        search={{
          value: search.q ?? "",
          onChange: (value) => {
            set({ q: value }, true);
          },
          label: t("fleet.certificates.searchLabel"),
          placeholder: t("fleet.certificates.searchPlaceholder"),
        }}
        filters={
          <>
            <ServerFilter
              view={view.data}
              value={search.server}
              onChange={(server) => {
                set({ server });
              }}
            />
            <StateFilter
              value={search.state}
              onChange={(state) => {
                set({ state });
              }}
              options={CERT_STATES.map((state) => ({ value: state, label: t(`fleet.certificates.state.${state}`) }))}
            />
          </>
        }
        count={
          view.data === undefined
            ? ""
            : isFiltered(search)
              ? t("fleet.certificates.countFiltered", { shown: shown.length, total: rows.length })
              : t("fleet.certificates.count", { count: rows.length })
        }
        actions={
          <Button
            icon={<Play aria-hidden="true" />}
            onClick={() => {
              actions.open({ action: "certs_renew" });
            }}
          >
            {t("fleet.certificates.renew")}
          </Button>
        }
      />
      <DataTable
        caption={t("fleet.certificates.caption")}
        columns={columns(t, view.outcomes)}
        rows={shown}
        getRowId={(row) => `${originOf(row).node}:${field(row, "domain") ?? ""}`}
        loading={view.isPending}
        skeletonRows={5}
        mobile="cards"
        empty={
          rows.length === 0 && view.data !== undefined ? (
            <EmptyState variant="inline" title={t("fleet.certificates.empty")} />
          ) : (
            <EmptyState
              variant="inline"
              title={t("fleet.filters.noMatch")}
              action={
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    onSearchChange({});
                  }}
                >
                  {t("fleet.filters.clear")}
                </Button>
              }
            />
          )
        }
      />
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust fleet certs" />
    </div>
  );
}
