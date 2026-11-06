/**
 * Processes: what runs on the machine and what it costs, read only (the console never signals a process). The question
 * is "which of mine is this?", so each process says the application or the service it belongs to, and the page can be
 * searched by that as well as by the name; "which one has a port?" is a link to the page that answers it.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { appsQuery } from "../../../api/queries/apps";
import { FilterBar } from "../../../components/page/FilterBar";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column, SortState } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { formatBytes, formatPercent } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { ServerQueryState } from "../errors";
import { OwnerName } from "../OwnerName";
import { ownerOfProcess, processWords } from "../processOwner";
import { SERVER_CAPABILITY, processesQuery } from "../queries";
import type { ProcessSort, Processes } from "../queries";

/** Processes listed at first, and after "Show more". */
const FEW = 10;
const MANY = 50;

/**
 * Each column's own direction: the busiest first for CPU and memory, in order for the PID and
 * the name. The server sorts, so the list is the busiest processes, not the first ten re-sorted.
 */
const NATURAL: Record<ProcessSort, SortState["direction"]> = {
  cpu: "descending",
  memory: "descending",
  pid: "ascending",
  name: "ascending",
};

function isProcessSort(column: string): column is ProcessSort {
  return column in NATURAL;
}

type ProcessRow = Processes["processes"][number];
type UnitRow = Processes["units"][number];

function Content() {
  const t = useT();
  const [sort, setSort] = useState<ProcessSort>("cpu");
  const [byUnit, setByUnit] = useState(false);
  const [limit, setLimit] = useState(FEW);
  const [query, setQuery] = useState("");
  const searching = query.trim() !== "";
  // A search looks through the longer list: what it is looking for is not always among the ten busiest.
  const processes = useQuery(processesQuery(sort, byUnit, searching ? MANY : limit));
  const apps = useQuery(appsQuery());
  const owners = useMemo(() => apps.data?.apps ?? [], [apps.data]);
  const needle = query.trim().toLowerCase();
  const kept = useMemo(
    () => (processes.data?.processes ?? []).filter((row) => needle === "" || processWords(row, ownerOfProcess(row, owners)).includes(needle)),
    [processes.data, owners, needle],
  );
  const keptUnits = useMemo(() => (processes.data?.units ?? []).filter((row) => needle === "" || row.unit.toLowerCase().includes(needle)), [processes.data, needle]);

  const processColumns: Column<ProcessRow>[] = [
    {
      id: "name",
      header: t("server.processes.process"),
      card: "title",
      sortValue: (row) => row.name,
      // The command line can be pages long: it is the name's tooltip, not a line of the row.
      cell: (row) => <Mono {...(row.command ? { title: row.command } : {})}>{row.name}</Mono>,
    },
    { id: "owner", header: t("server.processes.belongsTo"), hideBelow: "sm", cell: (row) => <OwnerName owner={ownerOfProcess(row, owners)} /> },
    { id: "pid", header: t("server.processes.pid"), align: "end", width: "w-20", hideBelow: "lg", sortValue: (row) => row.pid, cell: (row) => String(row.pid), mono: true },
    { id: "user", header: t("server.processes.user"), hideBelow: "lg", cell: (row) => <Mono tone="muted">{row.user}</Mono> },
    { id: "cpu", header: t("server.processes.cpu"), align: "end", width: "w-20", mono: true, sortValue: (row) => row.cpu_percent, cell: (row) => formatPercent(row.cpu_percent, t.locale) },
    { id: "memory", header: t("server.processes.memory"), align: "end", width: "w-28", mono: true, sortValue: (row) => row.memory_mb, cell: (row) => formatBytes(row.memory_mb * 1024 ** 2, t.locale) },
  ];
  const unitColumns: Column<UnitRow>[] = [
    { id: "unit", header: t("server.processes.unit"), card: "title", cell: (row) => <Mono>{row.unit}</Mono> },
    { id: "count", header: t("server.processes.count"), align: "end", width: "w-24", mono: true, cell: (row) => String(row.processes) },
    { id: "cpu", header: t("server.processes.cpu"), align: "end", width: "w-20", mono: true, sortValue: (row) => row.cpu_percent, cell: (row) => formatPercent(row.cpu_percent, t.locale) },
    { id: "memory", header: t("server.processes.memory"), align: "end", width: "w-28", mono: true, sortValue: (row) => row.memory_mb, cell: (row) => formatBytes(row.memory_mb * 1024 ** 2, t.locale) },
  ];
  const tableSort: SortState = { column: sort, direction: NATURAL[sort] };
  // A header picks the column; its direction is the column's own (see NATURAL).
  const sortBy = (next: SortState): void => {
    if (isProcessSort(next.column)) setSort(next.column);
  };
  const empty = <EmptyState variant="inline" title={searching ? t("server.processes.noMatch") : t("server.processes.none")} />;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <FilterBar
        label={t("server.processes.filterLabel")}
        search={{ value: query, onChange: setQuery, label: t("server.processes.searchLabel"), placeholder: t("server.processes.searchPlaceholder") }}
        filters={
          <Checkbox
            label={t("server.processes.byUnit")}
            checked={byUnit}
            onCheckedChange={(next) => {
              setByUnit(next);
              // A service has no PID nor a name of its own to sort by.
              if (next && (sort === "pid" || sort === "name")) setSort("cpu");
            }}
          />
        }
        actions={
          <span className="text-13 text-fg-muted">
            {t.rich("server.processes.whichPort", {
              link: (
                <TextLink key="ports" to="/network/listening" size="ui">
                  {t("server.processes.whichPortLink")}
                </TextLink>
              ),
            })}
          </span>
        }
      />
      <ServerQueryState query={processes} label={t("server.processes.label")} skeleton={<DataTable caption={t("server.processes.caption")} columns={processColumns} rows={[]} getRowId={() => ""} loading skeletonRows={FEW} density="compact" />}>
        {(data) => {
          const total = data.total;
          const shown = byUnit ? data.units.length : data.processes.length;
          return (
            <div className="flex min-w-0 flex-col gap-2">
              {byUnit ? (
                <DataTable columns={unitColumns} rows={keptUnits} getRowId={(row) => row.unit} caption={t("server.processes.unitsCaption")} sort={tableSort} onSortChange={sortBy} density="compact" empty={empty} />
              ) : (
                <DataTable columns={processColumns} rows={kept} getRowId={(row) => String(row.pid)} caption={t("server.processes.caption")} sort={tableSort} onSortChange={sortBy} density="compact" empty={empty} />
              )}
              {!searching && limit === FEW && (byUnit ? shown >= FEW : total > FEW) ? (
                <div>
                  <Button size="sm" variant="ghost" onClick={() => setLimit(MANY)}>
                    {byUnit ? t("server.processes.showMore", { count: MANY }) : t("server.processes.showFirst", { count: MANY, total })}
                  </Button>
                </div>
              ) : null}
            </div>
          );
        }}
      </ServerQueryState>
    </div>
  );
}

/** The Processes section. */
export function ProcessesPage() {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <Content />
    </NodeCapabilityGate>
  );
}
