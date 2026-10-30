/**
 * Ports in use: one row per port, with what holds it in the product's words (an application, a
 * published port, a database, the web server) and who can reach it. A number typed in the search
 * box answers "who uses port 5432?", and a port nothing holds says it is free and offers to open it.
 */

import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { FilterBar } from "../../../components/page/FilterBar";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { textLinkClassName } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ServerQueryState } from "../errors";
import { firewallQuery, portsQuery } from "../queries";
import { verdictView } from "../security/FirewallView";
import { askedPort, isFree, matchRows, portRows } from "./data";
import type { Holder, PortRow } from "./data";

/** What holds a port, said the way a person says it. */
function HolderName({ t, holder }: { t: T; holder: Holder }) {
  switch (holder.kind) {
    case "web":
      return <span>{t("serverPorts.holder.web")}</span>;
    case "ssh":
      return <span>{t("serverPorts.holder.ssh")}</span>;
    case "console":
      return <span>{t("serverPorts.holder.console")}</span>;
    case "dns":
      return <span>{t("serverPorts.holder.dns")}</span>;
    case "app":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
          <span>{t("serverPorts.holder.app")}</span>
          <Link to="/apps/$domain" params={{ domain: holder.owner }} className={textLinkClassName("inline", "min-w-0", "row")}>
            <Mono truncate>{holder.owner}</Mono>
          </Link>
        </span>
      );
    case "stack":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
          <span>{t("serverPorts.holder.stack")}</span>
          <Link to="/apps/$domain" params={{ domain: holder.owner }} className={textLinkClassName("inline", "min-w-0", "row")}>
            <Mono truncate>{holder.owner}</Mono>
          </Link>
        </span>
      );
    case "stream":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
          <span>{t("serverPorts.holder.stream")}</span>
          <Mono truncate>{holder.owner}</Mono>
        </span>
      );
    case "database":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
          <span>{t("serverPorts.holder.database")}</span>
          <Mono truncate>{holder.owner}</Mono>
        </span>
      );
    case "instance":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
          <span>{t("serverPorts.holder.instance")}</span>
          <Mono truncate>{holder.owner}</Mono>
        </span>
      );
    case "process":
      return (
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
          <span className="text-fg-muted">{t("serverPorts.holder.process")}</span>
          <Mono truncate>{holder.owner}</Mono>
        </span>
      );
  }
}

/** Who can reach a port: only a problem is coloured, and a port the firewall was not asked about says nothing. */
function Reach({ t, row }: { t: T; row: PortRow }) {
  if (row.firewall === null) return <span className="text-13 text-fg-faint">{t("serverPorts.notListening")}</span>;
  const view = verdictView(t, row.firewall);
  return view.state !== null ? <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" /> : <span className="text-13 text-fg-muted">{view.label}</span>;
}

export interface PortsViewProps {
  query: string;
  onQueryChange: (query: string) => void;
  /** Opens the dialog that opens a port, with this one filled in; absent when the firewall is off. */
  onOpen?: (port: number) => void;
}

/** The ports of the machine and who holds each. */
export function PortsView({ query, onQueryChange, onOpen }: PortsViewProps) {
  const t = useT();
  const claims = useQuery(portsQuery());
  // What the firewall says of each listening port is read beside it; the table stands without it.
  const firewall = useQuery(firewallQuery());
  return (
    <ServerQueryState query={claims} label={t("serverPorts.label")} skeleton={<SkeletonSection rows={5} />}>
      {(data) => {
        const rows = portRows(data.claims, firewall.data?.ports ?? []);
        const shown = matchRows(rows, query);
        const asked = askedPort(query);
        const free = asked !== null && isFree(rows, asked);
        const columns: Column<PortRow>[] = [
          {
            id: "port",
            header: t("serverPorts.column.port"),
            width: "w-32",
            card: "title",
            sortValue: (row) => row.port,
            cell: (row) => <Mono>{`${String(row.port)}/${row.protocol}`}</Mono>,
          },
          {
            id: "holder",
            header: t("serverPorts.column.holder"),
            cell: (row) => (
              <span className="flex min-w-0 flex-col gap-0.5">
                {row.holders.map((holder) => (
                  <HolderName key={`${holder.kind}:${holder.owner}:${holder.address ?? ""}`} t={t} holder={holder} />
                ))}
              </span>
            ),
          },
          { id: "reach", header: t("serverPorts.column.reach"), width: "w-60", card: "status", cell: (row) => <Reach t={t} row={row} /> },
        ];
        return (
          <div className="flex min-w-0 flex-col gap-4">
            <FilterBar
              label={t("serverPorts.filterLabel")}
              search={{ value: query, onChange: onQueryChange, label: t("serverPorts.searchLabel"), placeholder: t("serverPorts.searchPlaceholder") }}
              count={query.trim() === "" ? t("serverPorts.count", { count: rows.length }) : t("serverPorts.countFiltered", { shown: shown.length, total: rows.length })}
            />
            {free ? (
              <InlineMessage
                tone="success"
                trailing={
                  onOpen !== undefined ? (
                    <Button size="sm" onClick={() => onOpen(asked)}>
                      {t("serverPorts.openThis", { port: asked })}
                    </Button>
                  ) : undefined
                }
              >
                {t("serverPorts.free", { port: asked })}
              </InlineMessage>
            ) : null}
            <DataTable
              caption={t("serverPorts.caption")}
              columns={columns}
              rows={shown}
              getRowId={(row) => row.key}
              density="compact"
              empty={<EmptyState variant="inline" title={t("serverPorts.none")} />}
            />
          </div>
        );
      }}
    </ServerQueryState>
  );
}
