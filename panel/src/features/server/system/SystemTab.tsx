/**
 * The machine itself: four cards (the clock, the host name, the operating system, the power)
 * and, below them, one of three views chosen in the URL: the processes (read only: nothing here
 * signals a process), the network interfaces, or Noust's resource monitor.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { networkQuery } from "../../../api/queries/system";
import type { NetworkInfo } from "../../../api/queries/system";
import { CommandHint } from "../../../components/page/CommandHint";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import { formatBytes, formatPercent } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { ServerErrorBlock } from "../errors";
import { MonitorCard } from "../MonitorCard";
import { SERVER_CAPABILITY, processesQuery } from "../queries";
import type { ProcessSort, Processes } from "../queries";
import { ClockCard, HostnameCard, PowerCard, SystemCard } from "./SystemCards";
import type { SystemView } from "./data";

/** Processes listed at first, and after "Show more". */
const FEW = 10;
const MANY = 50;

type ProcessRow = Processes["processes"][number];
type UnitRow = Processes["units"][number];

function ProcessesView() {
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
      // The command line can be pages long: it is the name's tooltip, not a line of the row.
      cell: (row) => <Mono {...(row.command ? { title: row.command } : {})}>{row.name}</Mono>,
    },
    { id: "pid", header: t("server.processes.pid"), align: "end", width: "w-20", mono: true, cell: (row) => String(row.pid) },
    { id: "user", header: t("server.processes.user"), hideBelow: "sm", cell: (row) => <Mono tone="muted">{row.user}</Mono> },
    { id: "unit", header: t("server.processes.unit"), hideBelow: "lg", cell: (row) => (row.unit ? <Mono tone="muted">{row.unit}</Mono> : <EmptyCell />) },
    { id: "cpu", header: t("server.processes.cpu"), align: "end", width: "w-20", mono: true, cell: (row) => formatPercent(row.cpu_percent, t.locale) },
    { id: "memory", header: t("server.processes.memory"), align: "end", width: "w-28", mono: true, cell: (row) => formatBytes(row.memory_mb * 1024 ** 2, t.locale) },
  ];
  const unitColumns: Column<UnitRow>[] = [
    { id: "unit", header: t("server.processes.unit"), card: "title", cell: (row) => <Mono>{row.unit}</Mono> },
    { id: "count", header: t("server.processes.count"), align: "end", width: "w-24", mono: true, cell: (row) => String(row.processes) },
    { id: "cpu", header: t("server.processes.cpu"), align: "end", width: "w-20", mono: true, cell: (row) => formatPercent(row.cpu_percent, t.locale) },
    { id: "memory", header: t("server.processes.memory"), align: "end", width: "w-28", mono: true, cell: (row) => formatBytes(row.memory_mb * 1024 ** 2, t.locale) },
  ];
  const total = processes.data?.total ?? 0;
  const shown = byUnit ? (processes.data?.units.length ?? 0) : (processes.data?.processes.length ?? 0);
  return (
    <Card level={2}
      title={t("server.processes.title")}
      description={t("server.processes.description")}
      padding="none"
      actions={
        <div className="flex flex-wrap items-center gap-3">
          <Switch label={t("server.processes.byUnit")} checked={byUnit} onCheckedChange={setByUnit} />
          <SegmentedControl<ProcessSort>
            label={t("server.processes.sortLabel")}
            value={sort}
            onValueChange={setSort}
            options={[
              { value: "cpu", label: t("server.processes.sortCpu") },
              { value: "memory", label: t("server.processes.sortMemory") },
            ]}
          />
        </div>
      }
    >
      {processes.isError && processes.data === undefined ? (
        <div className="px-5 pb-4">
          <ServerErrorBlock compact error={processes.error} title={t("server.processes.loadFailed")} onRetry={() => void processes.refetch()} />
        </div>
      ) : byUnit ? (
        <DataTable
          columns={unitColumns}
          rows={processes.data?.units ?? []}
          getRowId={(row) => row.unit}
          caption={t("server.processes.unitsCaption")}
          loading={processes.isPending}
          skeletonRows={limit}
          density="compact"
          mobile="cards"
          empty={<EmptyState variant="inline" title={t("server.processes.none")} />}
        />
      ) : (
        <DataTable
          columns={processColumns}
          rows={processes.data?.processes ?? []}
          getRowId={(row) => String(row.pid)}
          caption={t("server.processes.caption")}
          loading={processes.isPending}
          skeletonRows={limit}
          density="compact"
          mobile="cards"
          empty={<EmptyState variant="inline" title={t("server.processes.none")} />}
        />
      )}
      {processes.data !== undefined && limit === FEW && (byUnit ? shown >= FEW : total > FEW) ? (
        <div className="border-t border-border px-5 py-2">
          <Button size="sm" variant="ghost" onClick={() => setLimit(MANY)}>
            {t("server.processes.showMore", { count: MANY })}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

type Interface = NetworkInfo["interfaces"][number];

function NetworkView() {
  const t = useT();
  const network = useQuery(networkQuery());
  const columns: Column<Interface>[] = [
    { id: "name", header: t("server.network.interface"), card: "title", cell: (row) => <Mono>{row.name}</Mono> },
    {
      id: "state",
      header: t("server.network.state"),
      width: "w-28",
      card: "status",
      cell: (row) => <StatusPill state={row.is_up ? "running" : "stopped"} label={row.is_up ? t("server.network.up") : t("server.network.down")} appearance="inline" size="sm" />,
    },
    {
      id: "addresses",
      header: t("server.network.addresses"),
      cell: (row) =>
        (row.addresses?.length ?? 0) > 0 ? (
          <span className="flex min-w-0 flex-col">
            {(row.addresses ?? []).map((address) => (
              <Mono key={address.address} tone="muted" truncate>
                {address.address}
              </Mono>
            ))}
          </span>
        ) : (
          <EmptyCell reason={t("server.network.noAddress")} />
        ),
    },
    { id: "sent", header: t("server.network.sent"), align: "end", hideBelow: "md", mono: true, cell: (row) => formatBytes(row.bytes_sent, t.locale) },
    { id: "received", header: t("server.network.received"), align: "end", hideBelow: "md", mono: true, cell: (row) => formatBytes(row.bytes_recv, t.locale) },
  ];
  return (
    <Card level={2} title={t("server.network.title")} padding="none">
      {network.isError && network.data === undefined ? (
        <div className="px-5 pb-4">
          <ServerErrorBlock compact error={network.error} title={t("server.network.loadFailed")} onRetry={() => void network.refetch()} />
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={network.data?.interfaces ?? []}
          getRowId={(row) => row.name}
          caption={t("server.network.caption")}
          loading={network.isPending}
          skeletonRows={2}
          density="compact"
          mobile="cards"
          empty={<EmptyState variant="inline" title={t("server.network.none")} />}
        />
      )}
    </Card>
  );
}

export interface SystemTabProps {
  view: SystemView;
  onViewChange: (view: SystemView) => void;
}

function System({ view, onViewChange }: SystemTabProps) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2 2xl:grid-cols-4">
        <ClockCard />
        <HostnameCard />
        <SystemCard />
        <PowerCard />
      </div>
      <SegmentedControl<SystemView>
        label={t("server.system.viewsLabel")}
        value={view}
        onValueChange={onViewChange}
        options={[
          { value: "processes", label: t("server.system.views.processes") },
          { value: "network", label: t("server.system.views.network") },
          { value: "monitor", label: t("server.system.views.monitor") },
        ]}
        className="self-start"
      />
      {view === "network" ? <NetworkView /> : view === "monitor" ? <MonitorCard /> : <ProcessesView />}
      <CommandHint command="noust server time status" label={t("server.fromTerminal")} />
    </div>
  );
}

/** The System tab. */
export function SystemTab(props: SystemTabProps) {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <System {...props} />
    </NodeCapabilityGate>
  );
}
