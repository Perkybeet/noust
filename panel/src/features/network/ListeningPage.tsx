/**
 * Listening ports, a section of Network and the one table of who listens on a port (the atlas found three: this
 * machine's Network, the published ports and the card of each database engine; the others now link here with the port
 * already searched). One row for each port: what holds it in Noust, the program the kernel names, and who can reach it
 * from outside. A port nothing holds says it is free and offers to open it; a port the firewall keeps shut offers the
 * same dialog every other place opens.
 */

import { useQuery } from "@tanstack/react-query";

import { FilterBar } from "../../components/page/FilterBar";
import { ReadOnlyLine, useCan } from "../../components/page/permissions";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Mono } from "../../components/ui/Mono";
import { SkeletonSection } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { NodeCapabilityGate } from "../../nodes/capability";
import { ServerQueryState } from "../server/errors";
import { MachinePage } from "../server/MachinePage";
import { askedPort, isFree, matchRows, portRows } from "../server/network/data";
import type { PortRow } from "../server/network/data";
import { SERVER_CAPABILITY, firewallQuery, portsQuery } from "../server/queries";
import { HolderName } from "./HolderName";
import { useOpenPort } from "./OpenPortDialog";
import { isShut, verdictView } from "./reach";

/** The program the kernel names for a port, or the container Docker publishes it for. */
export function programOf(row: PortRow): string | null {
  const port = row.firewall;
  if (port?.docker != null) return port.docker.project ? `${port.docker.project} / ${port.docker.container}` : port.docker.container;
  return port?.process ?? row.holders.find((holder) => holder.kind === "process")?.owner ?? null;
}

/** Who can reach a port: only a problem is coloured, and a port the firewall was not asked about says nothing. */
function Reach({ t, row }: { t: T; row: PortRow }) {
  if (row.firewall === null) return <span className="text-13 text-fg-faint">{t("serverPorts.notListening")}</span>;
  const view = verdictView(t, row.firewall);
  return view.state !== null ? <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" /> : <span className="text-13 text-fg-muted">{view.label}</span>;
}

export interface ListeningPageProps {
  query: string;
  onQueryChange: (query: string) => void;
}

function Listening({ query, onQueryChange }: ListeningPageProps) {
  const t = useT();
  const mayChange = useCan("server.host_access");
  const open = useOpenPort();
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
            cell: (row) => (
              <span className="flex items-center gap-2">
                <Mono>{String(row.port)}</Mono>
                <span className="text-12 text-fg-muted">{row.protocol.toUpperCase()}</span>
              </span>
            ),
          },
          {
            id: "program",
            header: t("serverPorts.column.program"),
            hideBelow: "md",
            card: "meta",
            cell: (row) => {
              const program = programOf(row);
              return program === null ? <EmptyCell reason={t("serverPorts.noProgram")} /> : <Mono>{program}</Mono>;
            },
          },
          {
            id: "holder",
            header: t("serverPorts.column.holder"),
            hideBelow: "sm",
            card: "meta",
            cell: (row) =>
              row.holders.length === 0 ? (
                <EmptyCell reason={t("serverPorts.noHolder")} />
              ) : (
                <span className="flex min-w-0 flex-col gap-0.5">
                  {row.holders.map((holder) => (
                    <HolderName key={`${holder.kind}:${holder.owner}:${holder.address ?? ""}`} holder={holder} />
                  ))}
                </span>
              ),
          },
          { id: "reach", header: t("serverPorts.column.reach"), width: "w-60", card: "status", cell: (row) => <Reach t={t} row={row} /> },
        ];
        return (
          <div className="flex min-w-0 flex-col gap-4">
            <ReadOnlyLine permission="server.host_access" />
            <FilterBar
              label={t("serverPorts.filterLabel")}
              search={{ value: query, onChange: onQueryChange, label: t("serverPorts.searchLabel"), placeholder: t("serverPorts.searchPlaceholder") }}
              count={query.trim() === "" ? t("serverPorts.count", { count: rows.length }) : t("serverPorts.countFiltered", { shown: shown.length, total: rows.length })}
            />
            {free ? (
              <InlineMessage
                tone="success"
                trailing={
                  mayChange ? (
                    <Button size="sm" onClick={() => open.open({ port: asked })}>
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
              {...(mayChange
                ? {
                    rowActions: (row: PortRow) =>
                      isShut(row.firewall) ? (
                        <Button size="sm" onClick={() => open.open({ port: row.port, proto: row.protocol })}>
                          {t("serverPorts.openOutside")}
                        </Button>
                      ) : null,
                  }
                : {})}
            />
            {open.dialog}
          </div>
        );
      }}
    </ServerQueryState>
  );
}

/** Listening ports, the one table of who listens on a port. */
export function ListeningPage(props: ListeningPageProps) {
  const t = useT();
  return (
    <MachinePage title={t("serverPorts.views.ports")} description={t("serverPorts.intro")}>
      <NodeCapabilityGate capability={SERVER_CAPABILITY}>
        <Listening {...props} />
      </NodeCapabilityGate>
    </MachinePage>
  );
}
