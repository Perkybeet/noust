import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useId, useState } from "react";

import { networkQuery, processesQuery, systemHealthQuery, systemInfoQuery, versionQuery } from "../../api/queries/system";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { StatTile } from "../../components/page/StatTile";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes, formatLoad, formatPercent } from "../../lib/format";
import { MonitorCard } from "./MonitorCard";
import { checkName, checkView, healthReasons, verdictText, verdictView } from "./data";
import type { HealthCheck, HealthReason } from "./data";

/** The checks `wasm health` runs on a typical machine, for the placeholder's height. */
const TYPICAL_CHECKS = 6;
/** Reasons a report usually gives when it gives any: an app down, a certificate close to expiry. */
const TYPICAL_REASONS = 2;
/** Mounts a server usually has: the root, a boot partition, a data volume. */
const TYPICAL_DISKS = 3;
/** The loopback and one network card. */
const TYPICAL_INTERFACES = 2;
/** The processes the table asks for; a machine always runs at least that many. */
const PROCESS_LIMIT = 25;

/** One reason for the verdict, in the report's words; a certificate it names links to that certificate. */
function ReasonText({ reason }: { reason: HealthReason }) {
  const mention = reason.certificate;
  if (mention === null) return <>{reason.message}</>;
  return (
    <>
      {mention.before}
      <Link
        to="/domains"
        search={{ q: mention.name }}
        translate="no"
        className="rounded-[4px] font-medium text-accent-fg underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-focus"
      >
        {mention.name}
      </Link>
      {mention.after}
    </>
  );
}

/**
 * Why the verdict is what it is: every issue (what fails the check) and warning (what only
 * needs attention) the report gives, each with its level as colour, shape and word.
 */
function HealthReasons({ t, reasons }: { t: T; reasons: readonly HealthReason[] }) {
  const headingId = useId();
  return (
    // Live, so a reason that appears while the page is open (a refresh after a certificate
    // expired) is read out with the verdict it changed; the first render is not announced.
    <div aria-labelledby={headingId} role="group" aria-live="polite" className="flex min-w-0 flex-col py-2">
      <h3 id={headingId} className="py-1 text-12 font-medium text-fg-muted">
        {t("server.health.reasonsLabel")}
      </h3>
      {reasons.length === 0 ? (
        <p className="py-1 text-13 text-fg-muted">{t("server.health.reasonsEmpty")}</p>
      ) : (
        <ul className="flex flex-col">
          {reasons.map((reason, index) => (
            <li key={`${reason.level}-${String(index)}`} className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-baseline gap-x-2 py-1">
              <StatusPill
                state={reason.level === "issue" ? "failed" : "warning"}
                label={reason.level === "issue" ? t("server.health.critical") : t("server.health.warning")}
                appearance="inline"
                size="sm"
              />
              <span className="text-13 text-pretty text-fg">
                <ReasonText reason={reason} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function HealthChecks({ t, checks }: { t: T; checks: readonly HealthCheck[] }) {
  return (
    <ul aria-label={t("server.health.checksLabel")} className="flex flex-col divide-y divide-border">
      {checks.map((check) => {
        const view = checkView(check.status);
        return (
          <li key={check.name} className="flex items-start justify-between gap-3 py-2">
            <span className="flex shrink-0 items-center gap-2 text-13 whitespace-nowrap text-fg">
              <StatusGlyph state={view.state} size={10} className={stateTextClass(view.state)} />
              {checkName(t, check.name)}
            </span>
            <span className="min-w-0 text-right text-13 text-pretty text-fg-muted">{check.value}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** The loaded card's shape: the verdict's pill, then one row per check. */
function HealthSkeleton({ t }: { t: T }) {
  return (
    <div aria-busy="true" className="rounded-card border border-border bg-surface px-4 shadow-raised">
      <span className="sr-only">{t("server.health.running")}</span>
      <div aria-hidden="true">
        <div className="flex h-12 items-center border-b border-border">
          <Skeleton className="h-6 w-20 rounded-pill" />
        </div>
        <div className="grid min-w-0 gap-x-6 max-lg:divide-y max-lg:divide-border lg:grid-cols-2">
          {/* The reasons' heading and a couple of them: the count is not known until the answer. */}
          <div className="flex flex-col py-2">
            <div className="flex h-6 items-center">
              <Skeleton className="h-3 w-16" />
            </div>
            {Array.from({ length: TYPICAL_REASONS }, (_, index) => (
              <div key={index} className="flex h-7 items-center gap-2">
                <Skeleton className="h-3 w-[5.5rem]" />
                <Skeleton className="h-3 w-64 max-w-full" />
              </div>
            ))}
          </div>
          <div className="flex flex-col divide-y divide-border lg:border-l lg:border-border lg:pl-6">
            {Array.from({ length: TYPICAL_CHECKS }, (_, index) => (
              <div key={index} className="flex h-9 items-center justify-between gap-3">
                <Skeleton className="h-3 w-28" />
                <Skeleton className="h-3 w-40" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Health() {
  const t = useT();
  const health = useQuery(systemHealthQuery());
  return (
    <Section title={t("server.health.title")} description={t("server.health.description")}>
      {health.isError && health.data === undefined ? (
        <ErrorBlock compact error={health.error} title={t("server.health.couldNotRun")} onRetry={() => void health.refetch()} />
      ) : health.data === undefined ? (
        <HealthSkeleton t={t} />
      ) : (
        <div className="rounded-card border border-border bg-surface px-4 shadow-raised">
          {/* A status region like the overview's announcements: the report refreshes in place,
              and a verdict that turns critical meanwhile must be heard, not only seen. */}
          <div role="status" aria-label={t("server.health.verdictLabel")} aria-atomic="true" className="flex items-center gap-2 border-b border-border py-3">
            <StatusPill state={verdictView(health.data.verdict).state} label={verdictText(t, verdictView(health.data.verdict).label)} />
          </div>
          {/* The reasons beside the checks on a wide screen, so a verdict never stands without them. */}
          <div className="grid min-w-0 gap-x-6 max-lg:divide-y max-lg:divide-border lg:grid-cols-2">
            <HealthReasons t={t} reasons={healthReasons(health.data)} />
            <div className="min-w-0 lg:border-l lg:border-border lg:pl-6">
              <HealthChecks t={t} checks={health.data.checks} />
            </div>
          </div>
        </div>
      )}
      <CommandHint command="wasm health" label={t("server.fromTerminal")} />
    </Section>
  );
}

/** The installed version, and the one released if a check found a newer one. */
function VersionTile() {
  const t = useT();
  const version = useQuery(versionQuery());
  if (version.data === undefined) return <StatTile label={t("server.system.version")} value={<Skeleton className="h-5 w-16" />} />;
  const { current_version, has_update, latest_version } = version.data;
  // New in 2.3; read loosely until the generated schema carries them.
  const { update_state, published_version } = version.data as {
    update_state?: string | null;
    published_version?: string | null;
  };
  const detail =
    has_update && latest_version
      ? t("server.system.updateAvailable", { version: latest_version })
      : update_state === "on_the_way" && published_version
        ? t("server.system.onTheWay", { version: published_version })
        : t("server.system.upToDate");
  return <StatTile label={t("server.system.version")} value={current_version} mono detail={detail} />;
}

function SystemInfo() {
  const t = useT();
  const info = useQuery(systemInfoQuery());
  if (info.isError && info.data === undefined) {
    return (
      <Section title={t("server.system.title")}>
        <ErrorBlock compact error={info.error} title={t("server.system.couldNotRead")} onRetry={() => void info.refetch()} />
      </Section>
    );
  }
  if (info.data === undefined) {
    // The tiles themselves with placeholder readings, and the disks table's own placeholder:
    // the loaded section's shape, so what follows it does not move when the answer lands.
    const pending = <Skeleton className="h-4 w-20" />;
    const labels = [t("server.system.kernel"), t("server.system.cpu"), t("server.system.memory"), t("server.system.version")];
    return (
      <Section title={t("server.system.title")}>
        <div aria-busy="true" className="flex flex-col gap-4">
          <span className="sr-only">{t("server.system.loading")}</span>
          <div aria-hidden="true" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
            <StatTile
              label={t("server.system.host")}
              value={pending}
              detail={<Skeleton className="my-0.5 h-3 w-24" />}
              className="col-span-2 lg:col-span-1"
            />
            {labels.map((label) => (
              <StatTile key={label} label={label} value={pending} detail={<Skeleton className="my-0.5 h-3 w-24" />} />
            ))}
          </div>
          <Disks disks={[]} loading />
        </div>
      </Section>
    );
  }
  const { hostname, os, kernel, uptime, cpu, memory, disks } = info.data;
  return (
    <Section title={t("server.system.title")}>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        {/* Two columns on a phone make five tiles an orphan: the host takes a row of its own. */}
        <StatTile label={t("server.system.host")} value={hostname} mono detail={os} className="col-span-2 lg:col-span-1" />
        <StatTile label={t("server.system.kernel")} value={kernel} mono detail={t("server.system.upSince", { uptime })} />
        <StatTile
          label={t("server.system.cpu")}
          value={formatPercent(cpu.percent)}
          detail={t("server.system.cpuDetail", {
            cores: cpu.cores,
            load1: formatLoad(cpu.load_1min, t.locale),
            load5: formatLoad(cpu.load_5min, t.locale),
            load15: formatLoad(cpu.load_15min, t.locale),
          })}
        />
        <StatTile
          label={t("server.system.memory")}
          value={formatPercent(memory.percent_used)}
          detail={t("server.system.memoryDetail", {
            used: formatBytes(memory.used_gb * 1024 ** 3),
            total: formatBytes(memory.total_gb * 1024 ** 3),
          })}
        />
        <VersionTile />
      </div>
      <Disks disks={disks} />
    </Section>
  );
}

function Disks({
  disks,
  loading = false,
}: {
  disks: readonly { device: string; mount_point: string; total_gb: number; used_gb: number; percent_used: number }[];
  loading?: boolean;
}) {
  const t = useT();
  const columns: Column<(typeof disks)[number]>[] = [
    {
      id: "mount",
      header: t("server.system.columnMount"),
      mono: true,
      // Container runtimes mount paths a hundred characters long; the row keeps its numbers in
      // view and the whole path stays in the text (and on hover).
      cell: (row) => (
        <span title={row.mount_point} className="block max-w-[10rem] truncate sm:max-w-[28rem]">
          {row.mount_point}
        </span>
      ),
      sortValue: (row) => row.mount_point,
    },
    { id: "device", header: t("server.system.columnDevice"), mono: true, hideBelow: "sm", cell: (row) => row.device, sortValue: (row) => row.device },
    {
      id: "used",
      header: t("server.system.columnUsed"),
      align: "end",
      mono: true,
      cell: (row) => formatBytes(row.used_gb * 1024 ** 3),
      sortValue: (row) => row.used_gb,
    },
    {
      id: "total",
      header: t("server.system.columnTotal"),
      align: "end",
      mono: true,
      hideBelow: "sm",
      cell: (row) => formatBytes(row.total_gb * 1024 ** 3),
      sortValue: (row) => row.total_gb,
    },
    {
      id: "percent",
      header: t("server.system.columnUse"),
      align: "end",
      mono: true,
      cell: (row) => formatPercent(row.percent_used),
      sortValue: (row) => row.percent_used,
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={disks}
      getRowId={(row) => row.mount_point}
      caption={t("server.system.disksCaption")}
      loading={loading}
      skeletonRows={TYPICAL_DISKS}
      defaultSort={{ column: "mount", direction: "ascending" }}
      empty={<p className="p-4 text-13 text-fg-muted">{t("server.system.disksEmpty")}</p>}
    />
  );
}

function Network() {
  const t = useT();
  const network = useQuery(networkQuery());
  const interfaces = network.data?.interfaces ?? [];
  const columns: Column<(typeof interfaces)[number]>[] = [
    {
      id: "name",
      header: t("server.network.columnInterface"),
      mono: true,
      cell: (row) => (
        <span className="flex items-center gap-2">
          <StatusGlyph state={row.is_up ? "running" : "stopped"} size={10} className={row.is_up ? "text-ok" : "text-fg-faint"} />
          {row.name}
        </span>
      ),
      sortValue: (row) => row.name,
    },
    {
      id: "addresses",
      header: t("server.network.columnAddresses"),
      mono: true,
      hideBelow: "sm",
      cell: (row) => ((row.addresses?.length ?? 0) > 0 ? (row.addresses ?? []).map((a) => a.address).join(", ") : "-"),
    },
    {
      id: "sent",
      header: t("server.network.columnSent"),
      align: "end",
      mono: true,
      hideBelow: "md",
      cell: (row) => formatBytes(row.bytes_sent),
      sortValue: (row) => row.bytes_sent,
    },
    {
      id: "recv",
      header: t("server.network.columnReceived"),
      align: "end",
      mono: true,
      hideBelow: "md",
      cell: (row) => formatBytes(row.bytes_recv),
      sortValue: (row) => row.bytes_recv,
    },
  ];
  return (
    <Section title={t("server.network.title")}>
      {network.isError && network.data === undefined ? (
        <ErrorBlock compact error={network.error} title={t("server.network.couldNotRead")} onRetry={() => void network.refetch()} />
      ) : (
        <DataTable
          columns={columns}
          rows={interfaces}
          getRowId={(row) => row.name}
          caption={t("server.network.caption")}
          loading={network.isPending}
          skeletonRows={TYPICAL_INTERFACES}
          defaultSort={{ column: "name", direction: "ascending" }}
          empty={<p className="p-4 text-13 text-fg-muted">{t("server.network.empty")}</p>}
        />
      )}
    </Section>
  );
}

type SortBy = "cpu" | "memory" | "pid" | "name";

function sortOptions(t: T): readonly { value: SortBy; label: string }[] {
  return [
    { value: "cpu", label: t("server.processes.sortByCpu") },
    { value: "memory", label: t("server.processes.sortByMemory") },
    { value: "pid", label: t("server.processes.sortByPid") },
    { value: "name", label: t("server.processes.sortByName") },
  ];
}

function Processes() {
  const t = useT();
  const [sortBy, setSortBy] = useState<SortBy>("cpu");
  const processes = useQuery(processesQuery(sortBy, PROCESS_LIMIT));
  const rows = processes.data?.processes ?? [];
  const columns: Column<(typeof rows)[number]>[] = [
    { id: "pid", header: t("server.processes.columnPid"), mono: true, width: "w-16", cell: (row) => row.pid },
    { id: "name", header: t("server.processes.columnProcess"), mono: true, cell: (row) => row.name },
    { id: "user", header: t("server.processes.columnUser"), mono: true, hideBelow: "sm", cell: (row) => row.user },
    { id: "cpu", header: t("server.processes.columnCpu"), align: "end", mono: true, cell: (row) => formatPercent(row.cpu_percent) },
    {
      id: "memory",
      header: t("server.processes.columnMemory"),
      align: "end",
      mono: true,
      hideBelow: "sm",
      cell: (row) => (
        <span>
          {formatBytes(row.memory_mb * 1024 ** 2)} <span className="text-fg-faint">{formatPercent(row.memory_percent)}</span>
        </span>
      ),
    },
  ];
  return (
    <Section
      title={t("server.processes.title")}
      description={t("server.processes.description")}
      actions={
        <Select
          aria-label={t("server.processes.sortLabel")}
          size="sm"
          value={sortBy}
          onValueChange={setSortBy}
          options={sortOptions(t)}
        />
      }
    >
      {processes.isError && processes.data === undefined ? (
        <ErrorBlock compact error={processes.error} title={t("server.processes.couldNotList")} onRetry={() => void processes.refetch()} />
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          getRowId={(row) => String(row.pid)}
          // Distinct from the Section's own title: a <section> with an accessible name and a
          // region inside it named identically are two landmarks of the same name, which axe's
          // landmark-unique rule (and a screen reader's landmarks list) flags as a duplicate.
          caption={t("server.processes.caption")}
          loading={processes.isPending}
          skeletonRows={PROCESS_LIMIT}
          empty={<p className="p-4 text-13 text-fg-muted">{t("server.processes.empty")}</p>}
        />
      )}
    </Section>
  );
}

/** Health, hardware and processes of this machine, and the resource monitor's own controls. */
export function ServerPage() {
  const t = useT();
  return (
    <>
      <PageHeader title={t("server.title")} description={t("server.description")} />
      <div className="flex flex-col gap-8">
        <Health />
        <SystemInfo />
        <Network />
        <Processes />
        <MonitorCard />
      </div>
    </>
  );
}
