/**
 * Status, the machine in one screen: how loaded it is (the processor, the memory, the disk and how long it has been
 * up), what is using it right now and what is not well. The first answers "is it slow?", the second says which
 * process and whose it is, the third says what to do about each thing; every list it draws from is a section of its
 * own, linked from here.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { appsQuery } from "../../../api/queries/apps";
import { overviewQuery } from "../../../api/queries/overview";
import type { OverviewAttentionItem } from "../../../api/queries/overview";
import { machineQuery } from "../../../api/queries/system";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { FirstUse } from "../../../components/ui/FirstUse";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Meter } from "../../../components/ui/Progress";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusGlyph } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { formatBytes, formatBytesPair, formatDuration, formatPercent } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { AttentionRows } from "../../inbox/AttentionRows";
import { useAttention } from "../../inbox/useAttention";
import { serverItems } from "../attention";
import { ServerQueryState } from "../errors";
import { OwnerName } from "../OwnerName";
import { ownerOfProcess } from "../processOwner";
import { SERVER_CAPABILITY, identityQuery, processesQuery, summaryQuery } from "../queries";
import type { Processes, ServerSummary } from "../queries";

/** Rows of what is not well before "Show all": the three most serious and two more fit beside the processes. */
const SHOWN = 5;

/** The processes that cost the most, which is the first thing to look at when the machine is slow. */
const TOP_PROCESSES = 3;

/** What is not well on the machine, worst first: the machine's slice of the one list of what needs attention, each row with its button; nothing, said once. */
function NotWell({ items, total, loading }: { items: readonly OverviewAttentionItem[]; total: number; loading: boolean }) {
  const t = useT();
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <SurfaceSection
      title={t("server.attention.title")}
      icon={OBJECT_ICONS.alert}
      description={items.length > 0 && total > items.length ? t("server.attention.ofAll", { count: items.length, total }) : t("server.attention.description")}
      flush={items.length > 0 || loading}
      loading={loading}
      actions={
        items.length > SHOWN ? (
          <Button size="sm" variant="ghost" onClick={() => setAll((current) => !current)}>
            {all ? t("server.attention.showFewer") : t("server.attention.showAll", { count: items.length })}
          </Button>
        ) : undefined
      }
    >
      {loading ? (
        <LoadingRegion label={t("server.attention.loading")} className="flex flex-col gap-3 px-5 py-4">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-8 w-full" />
          ))}
        </LoadingRegion>
      ) : items.length === 0 ? (
        <p className="flex items-center gap-2 text-13 text-fg-muted">
          <ICONS.success aria-hidden="true" className="size-icon-sm text-ok" />
          {t("server.attention.nothing")}
        </p>
      ) : (
        <AttentionRows t={t} items={shown} label={t("server.attention.listLabel")} />
      )}
    </SurfaceSection>
  );
}

/** One of the four readings: what it is, of how much, and a bar that says how full it is. */
function Reading({ title, detail, label, value, children }: { title: string; detail: string | null; label: string; value: number | null; children?: ReactNode }) {
  return (
    <Card level={2} title={title} padding="sm" loading={value === null}>
      <div className="flex min-w-0 flex-col gap-3">
        <p className="min-h-4 text-12 text-fg-muted">{detail}</p>
        {value === null ? <Skeleton className="h-5 w-full" /> : <Meter size="sm" label={label} value={value} />}
        {children}
      </div>
    </Card>
  );
}

function Readings({ summary }: { summary: ServerSummary }) {
  const t = useT();
  const machine = useQuery(machineQuery());
  const overview = useQuery(overviewQuery());
  const identity = useQuery(identityQuery());
  const memory = machine.data?.memory;
  const disk = overview.data?.disk;
  const forecast = disk !== undefined && !disk.error && disk.forecast_full_days !== null && disk.forecast_full_days !== undefined ? Math.round(disk.forecast_full_days) : null;
  const reboot = summary.reboot.required === true;
  const rebootPackages = summary.reboot.packages ?? [];
  const [diskUsed, diskTotal] = disk !== undefined && !disk.error ? formatBytesPair(disk.used, disk.total, t.locale) : ["", ""];
  return (
    <div className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Reading
        title={t("server.status.cpu")}
        detail={identity.data !== undefined && machine.data !== undefined ? t("server.status.cpuDetail", { count: identity.data.cpu_count }) : null}
        label={t("server.status.inUse")}
        value={machine.data?.cpu_percent ?? null}
      />
      <Reading
        title={t("server.status.memory")}
        detail={memory !== undefined ? t("server.status.memoryDetail", { total: formatBytesPair(memory.used, memory.total, t.locale)[1] }) : null}
        label={t("server.status.inUse")}
        value={memory?.percent ?? null}
      />
      <Reading
        title={t("server.status.disk")}
        detail={disk !== undefined && !disk.error ? t("server.status.diskDetail", { used: diskUsed, total: diskTotal }) : null}
        label={t("server.status.inUse")}
        value={disk !== undefined && !disk.error ? disk.percent : null}
      >
        {forecast !== null ? <p className="text-12 text-fg-muted">{t("server.status.diskForecast", { count: forecast })}</p> : null}
      </Reading>
      <Card level={2} title={t("server.status.uptime")} padding="sm">
        <div className="flex min-w-0 flex-col gap-1.5">
          <p className="min-h-4 text-12 text-fg-muted">{t("server.status.uptimeSince")}</p>
          <p className="title text-24 text-fg">{summary.uptime_seconds != null ? formatDuration(summary.uptime_seconds, t.locale) : "-"}</p>
          {reboot ? (
            <p className="flex items-center gap-1.5 text-12 text-warn">
              <StatusGlyph state="warning" size={12} />
              <span className="text-fg-muted">
                {rebootPackages.length > 0 ? t("server.status.needsRebootFor", { packages: rebootPackages.slice(0, 2).join(", ") }) : t("server.status.needsReboot")}
              </span>
            </p>
          ) : null}
        </div>
      </Card>
    </div>
  );
}

/** What is spending the processor now, whose it is and how much memory it holds: where to start when it is slow. */
function TopProcesses() {
  const t = useT();
  const processes = useQuery(processesQuery("cpu", false, TOP_PROCESSES));
  const apps = useQuery(appsQuery());
  const columns = (owners: readonly { domain: string; unit?: string | null | undefined }[]): Column<Processes["processes"][number]>[] => [
    {
      id: "name",
      header: t("server.processes.process"),
      card: "title",
      cell: (row) => <Mono {...(row.command ? { title: row.command } : {})}>{row.name}</Mono>,
    },
    {
      id: "owner",
      header: t("server.processes.belongsTo"),
      hideBelow: "sm",
      cell: (row) => <OwnerName owner={ownerOfProcess(row, owners)} />,
    },
    { id: "cpu", header: t("server.processes.cpu"), align: "end", mono: true, cell: (row) => formatPercent(row.cpu_percent, t.locale) },
    { id: "memory", header: t("server.processes.memory"), align: "end", mono: true, cell: (row) => formatBytes(row.memory_mb * 1024 ** 2, t.locale) },
  ];
  return (
    <SurfaceSection
      title={t("server.status.heaviest")}
      icon={OBJECT_ICONS.processes}
      description={t("server.status.heaviestDescription")}
      flush
      actions={
        <TextLink to="/server/processes" size="ui">
          {t("server.status.allProcesses")}
        </TextLink>
      }
    >
      <ServerQueryState query={processes} label={t("server.processes.label")} skeleton={<DataTable embedded caption={t("server.processes.caption")} columns={columns([])} rows={[]} getRowId={() => ""} loading skeletonRows={TOP_PROCESSES} />}>
        {(data) => (
          <DataTable
            embedded
            columns={columns(apps.data?.apps ?? [])}
            rows={data.processes.slice(0, TOP_PROCESSES)}
            getRowId={(row) => String(row.pid)}
            caption={t("server.processes.caption")}
            density="compact"
            empty={<FirstUse>{t("server.processes.none")}</FirstUse>}
          />
        )}
      </ServerQueryState>
    </SurfaceSection>
  );
}

function OverviewLayout({ summary }: { summary: ServerSummary | undefined }) {
  const attention = useAttention();
  const items = serverItems(attention.items);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {summary !== undefined ? <Readings summary={summary} /> : <ReadingsSkeleton />}
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2">
        <TopProcesses />
        <NotWell items={items} total={attention.total} loading={summary === undefined || attention.loading} />
      </div>
    </div>
  );
}

/** The four readings before they are known: the same four cards, so nothing moves when they arrive. */
function ReadingsSkeleton() {
  const t = useT();
  return (
    <LoadingRegion label={t("server.overview.loading")} className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }, (_, index) => (
        <Card key={index} level={2} title={[t("server.status.cpu"), t("server.status.memory"), t("server.status.disk"), t("server.status.uptime")][index] ?? ""} padding="sm" loading>
          <Skeleton className="h-16 w-full" />
        </Card>
      ))}
    </LoadingRegion>
  );
}

function Overview() {
  const t = useT();
  const summary = useQuery(summaryQuery());
  return (
    <ServerQueryState query={summary} label={t("server.overview.label")} skeleton={<OverviewLayout summary={undefined} />}>
      {(data) => <OverviewLayout summary={data} />}
    </ServerQueryState>
  );
}

/** The Status section. */
export function OverviewTab() {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <Overview />
    </NodeCapabilityGate>
  );
}
