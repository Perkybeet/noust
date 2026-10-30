/**
 * System and updates: one of four views chosen in the URL. What is waiting to be updated comes first,
 * the system and Noust's own update (the task most people come for); then the machine itself (the
 * clock, the host name, the operating system, the power), what runs on it (read only: nothing here
 * signals a process) and what it uses, Noust's resource monitor. The network is Network and ports'.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CommandHint } from "../../../components/page/CommandHint";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column, SortState } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { formatBytes, formatPercent } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { ServerQueryState } from "../errors";
import { MonitorCard } from "../MonitorCard";
import { SERVER_CAPABILITY, identityQuery, processesQuery, summaryQuery, timeQuery } from "../queries";
import type { ProcessSort, Processes } from "../queries";
import { UpdatesTab } from "../updates/UpdatesTab";
import { ClockCard, HostnameCard, MachineSkeleton, PowerCard, SystemCard } from "./SystemCards";
import { NoustUpdate } from "./NoustUpdate";
import type { SystemView } from "./data";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

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

export function ProcessesView() {
  const t = useT();
  const [sort, setSort] = useState<ProcessSort>("cpu");
  const [byUnit, setByUnit] = useState(false);
  const [limit, setLimit] = useState(FEW);
  const processes = useQuery(processesQuery(sort, byUnit, limit));
  const processColumns: Column<ProcessRow>[] = [
    {
      id: "name",
      header: t("server.processes.process"),
      card: "title",
      sortValue: (row) => row.name,
      // The command line can be pages long: it is the name's tooltip, not a line of the row.
      cell: (row) => <Mono {...(row.command ? { title: row.command } : {})}>{row.name}</Mono>,
    },
    { id: "pid", header: t("server.processes.pid"), align: "end", width: "w-20", mono: true, sortValue: (row) => row.pid, cell: (row) => String(row.pid) },
    { id: "user", header: t("server.processes.user"), hideBelow: "sm", cell: (row) => <Mono tone="muted">{row.user}</Mono> },
    { id: "unit", header: t("server.processes.unit"), hideBelow: "lg", cell: (row) => (row.unit ? <Mono tone="muted">{row.unit}</Mono> : <EmptyCell />) },
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
  return (
    <ServerQueryState query={processes} label={t("server.processes.label")} skeleton={<SkeletonCard lines={limit} />}>
      {(data) => {
        const total = data.total;
        const shown = byUnit ? data.units.length : data.processes.length;
        return (
          <Card level={2}
            title={t("server.processes.title")} icon={OBJECT_ICONS.processes}
            description={t("server.processes.description")}
            padding="none"
            actions={
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
          >
            {byUnit ? (
              <DataTable
                columns={unitColumns}
                rows={data.units}
                getRowId={(row) => row.unit}
                caption={t("server.processes.unitsCaption")}
                sort={tableSort}
                onSortChange={sortBy}
                density="compact"
                empty={<EmptyState variant="inline" title={t("server.processes.none")} />}
              />
            ) : (
              <DataTable
                columns={processColumns}
                rows={data.processes}
                getRowId={(row) => String(row.pid)}
                caption={t("server.processes.caption")}
                sort={tableSort}
                onSortChange={sortBy}
                density="compact"
                empty={<EmptyState variant="inline" title={t("server.processes.none")} />}
              />
            )}
            {limit === FEW && (byUnit ? shown >= FEW : total > FEW) ? (
              <div className="border-t border-border px-5 py-2">
                <Button size="sm" variant="ghost" onClick={() => setLimit(MANY)}>
                  {byUnit
                    ? t("server.processes.showMore", { count: MANY })
                    : t("server.processes.showFirst", { count: MANY, total })}
                </Button>
              </div>
            ) : null}
          </Card>
        );
      }}
    </ServerQueryState>
  );
}

export interface SystemTabProps {
  view: SystemView;
  onViewChange: (view: SystemView) => void;
}

function MachineView() {
  const t = useT();
  const waiting = [useQuery(timeQuery()), useQuery(identityQuery()), useQuery(summaryQuery())].some((query) => query.isPending);
  // The cards answer apart and a row of two is as tall as the taller of them, so the one that arrived last moved the row under
  // it: nothing is drawn until the four are known, and what follows them is drawn with them.
  return waiting ? (
    <MachineSkeleton />
  ) : (
    <>
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2 2xl:grid-cols-4">
        <ClockCard />
        <HostnameCard />
        <SystemCard />
        <PowerCard />
      </div>
      <CommandHint command="noust server time status" label={t("common.words.fromTerminal")} />
    </>
  );
}

function System({ view, onViewChange }: SystemTabProps) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <SegmentedControl<SystemView>
        label={t("server.system.viewsLabel")}
        value={view}
        onValueChange={onViewChange}
        options={[
          { value: "updates", label: t("server.system.views.updates") },
          { value: "machine", label: t("server.system.views.machine") },
          { value: "processes", label: t("server.system.views.processes") },
          { value: "monitor", label: t("server.system.views.monitor") },
        ]}
        className="self-start"
      />
      {view === "updates" ? (
        <>
          <NoustUpdate />
          <UpdatesTab />
        </>
      ) : view === "machine" ? (
        <MachineView />
      ) : view === "monitor" ? (
        <MonitorCard />
      ) : (
        <ProcessesView />
      )}
    </div>
  );
}

/** The System and updates section. */
export function SystemTab(props: SystemTabProps) {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <System {...props} />
    </NodeCapabilityGate>
  );
}
