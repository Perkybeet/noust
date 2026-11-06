/**
 * The widgets about the machine: its three meters, the disk and when it fills, what uses the processor, the
 * services that are down, the updates that wait and what the security checks found. The readings that
 * move (CPU, memory) come from the event stream the top bar already listens to, so a panel of them asks
 * the server nothing more.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { overviewQuery } from "../../../api/queries/overview";
import { servicesQuery } from "../../../api/queries/services";
import { machineQuery, processesQuery } from "../../../api/queries/system";
import { fleetViewQuery, noustOf, noustUpdatable, osUpdatesOf, textOf } from "../../../features/fleet/data";
import { securityOverviewQuery } from "../../../features/server/queries";
import { useT } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatBytes, formatBytesPair, formatCount, formatPercent } from "../../../lib/format";
import { Meter } from "../../ui/Progress";
import { Mono } from "../../ui/Mono";
import { Big, Glyph, StateLine, WidgetPending } from "../parts";
import type { WidgetBodyProps } from "../types";

// ---------------------------------------------------------------------------------------
// Resources

/** The processor, the memory and the disk now: three meters that turn amber and red by themselves. */
export function ResourcesBody() {
  const t = useT();
  const machine = useQuery(machineQuery());
  if (machine.data === undefined) return <WidgetPending title={t("widgets.resources.title")} failed={machine.isError} onRetry={() => void machine.refetch()} />;
  const rows = [
    { id: "cpu", label: t("shell.machine.cpu"), value: machine.data.cpu_percent },
    { id: "memory", label: t("shell.machine.memory"), value: machine.data.memory.percent },
    { id: "disk", label: t("shell.machine.disk"), value: machine.data.disk.percent },
  ];
  return (
    <ul className="flex h-full flex-col justify-center gap-2.5 w-wide:grid w-wide:grid-cols-3 w-wide:gap-4 w-tall:justify-start w-tall:gap-4">
      {rows.map((row) => (
        <li key={row.id} className="min-w-0">
          <Meter size="sm" label={row.label} value={row.value} />
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------------------
// Disk

/** How full the disk is, and, when the history says it, when it will be. */
export function DiskBody() {
  const t = useT();
  const overview = useQuery(overviewQuery());
  if (overview.data === undefined) return <WidgetPending title={t("widgets.disk.title")} failed={overview.isError} onRetry={() => void overview.refetch()} />;
  const { disk } = overview.data;
  if (disk.error) return <p className="text-13 text-fg-muted">{t("widgets.disk.unavailable")}</p>;
  const [used, total] = formatBytesPair(disk.used, disk.total, t.locale);
  return (
    <div className="flex h-full flex-col gap-2">
      <Meter label={overview.data.server.name === "" ? t("shell.machine.disk") : t("widgets.disk.of", { server: overview.data.server.name })} value={disk.percent} valueText={formatPercent(disk.percent, t.locale)} />
      <p className="text-12 text-fg-muted">{t("widgets.disk.used", { used, total })}</p>
      <p className="hidden text-12 text-fg-muted w-tall:block">
        {disk.forecast_full_days !== null && disk.forecast_full_days !== undefined ? t("widgets.disk.full", { count: Math.round(disk.forecast_full_days) }) : t("widgets.disk.noForecast")}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Processes

const PROCESSES_SHOWN = 5;

/** The processes that use the most processor, with how much memory each holds. */
export function ProcessesBody() {
  const t = useT();
  const processes = useQuery(processesQuery("cpu", PROCESSES_SHOWN));
  if (processes.data === undefined) return <WidgetPending title={t("widgets.processes.title")} failed={processes.isError} onRetry={() => void processes.refetch()} />;
  return (
    <ul className="flex h-full flex-col gap-1.5 overflow-hidden [&>li:nth-child(n+4)]:hidden w-tall:[&>li:nth-child(n+4)]:flex">
      {processes.data.processes.slice(0, PROCESSES_SHOWN).map((process) => (
        <li key={process.pid} className="flex items-center gap-3 text-13">
          <Mono className="w-20 shrink-0 truncate">{process.name}</Mono>
          <span className="elev-carved-fine relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-pill bg-bg-sunken">
            <span className="absolute inset-y-0 left-0 rounded-pill bg-il-stroke" style={{ width: `${String(Math.min(100, process.cpu_percent))}%` }} />
          </span>
          <span className="w-10 shrink-0 text-right text-12 text-fg tabular-nums">{formatPercent(process.cpu_percent, t.locale)}</span>
          <span className="hidden w-16 shrink-0 text-right text-12 text-fg-muted tabular-nums w-mid:inline">{formatBytes(process.memory_mb * 1024 * 1024, t.locale)}</span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------------------
// Services

/** The services that are not running, and how many of the others are. */
export function ServicesBody() {
  const t = useT();
  const services = useQuery(servicesQuery());
  if (services.data === undefined) return <WidgetPending title={t("widgets.services.title")} failed={services.isError} onRetry={() => void services.refetch()} />;
  const down = services.data.services.filter((service) => service.active_state === "failed");
  const well = services.data.services.length - down.length;
  if (down.length === 0) return <StateLine state="running">{t("widgets.services.allRunning", { count: well })}</StateLine>;
  return (
    <ul className="flex h-full flex-col gap-1.5 overflow-hidden">
      {down.slice(0, 4).map((service) => (
        <li key={service.name} className="flex items-center gap-2 text-13">
          <Glyph state="failed" />
          <Link to="/server/services/$name" params={{ name: service.name }} className="min-w-0 flex-1 truncate hover:underline">
            <Mono>{service.name}</Mono>
          </Link>
        </li>
      ))}
      <li className="text-12 text-fg-muted">{t("widgets.services.others", { count: well })}</li>
    </ul>
  );
}

// ---------------------------------------------------------------------------------------
// Updates

/** The packages of the system that wait, and, for the fleet, the servers that have something to update and their Noust. */
export function UpdatesBody({ scope }: WidgetBodyProps) {
  const t = useT();
  const overview = useQuery({ ...overviewQuery(), enabled: scope === "server" });
  const fleet = useQuery({ ...fleetViewQuery("updates"), enabled: scope === "fleet" });
  const title = t("widgets.updates.title");
  if (scope === "fleet") {
    if (fleet.data === undefined) return <WidgetPending title={title} failed={fleet.isError} onRetry={() => void fleet.refetch()} />;
    const rows = fleet.data.items.map((row) => ({ name: textOf(row["name"]) ?? textOf(row["node"]) ?? "", noust: noustOf(row), os: osUpdatesOf(row) }));
    const pending = rows.reduce((sum, row) => sum + (row.os?.pending ?? 0), 0);
    const security = rows.reduce((sum, row) => sum + (row.os?.security ?? 0), 0);
    const outdated = rows.filter((row) => noustUpdatable(row.noust.state)).length;
    return (
      <div className="flex h-full flex-col gap-2">
        <div className="flex items-baseline gap-2">
          <Big>{formatCount(pending, t.locale)}</Big>
          <span className="text-13 text-fg-muted">{t("widgets.updates.packages")}</span>
        </div>
        {security > 0 ? <StateLine state="warning">{t("widgets.updates.security", { count: security })}</StateLine> : null}
        {outdated > 0 ? <StateLine state="warning">{t("widgets.updates.noustBehind", { count: outdated })}</StateLine> : null}
        <ul className="mt-1 hidden min-h-0 flex-col gap-1.5 overflow-hidden w-wide:flex">
          {rows.map((row) => (
            <li key={row.name} className="flex items-center gap-3 text-13">
              <span translate="no" className="w-24 shrink-0 truncate font-medium text-fg">
                {row.name}
              </span>
              <span className="min-w-0 flex-1 truncate text-fg-muted">
                {noustUpdatable(row.noust.state) && row.noust.latest !== null ? t("widgets.updates.noustAvailable", { version: row.noust.latest }) : t("widgets.updates.noustCurrent")}
              </span>
              <span className="text-12 text-fg-faint">{row.os?.pending !== null && row.os?.pending !== undefined && row.os.pending > 0 ? t("widgets.updates.count", { count: row.os.pending }) : t("widgets.updates.upToDate")}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (overview.data === undefined) return <WidgetPending title={title} failed={overview.isError} onRetry={() => void overview.refetch()} />;
  const { updates } = overview.data;
  if (updates.supported === false) return <p className="text-13 text-fg-muted">{t("widgets.figures.updatesUnsupported")}</p>;
  const available = updates.available ?? null;
  if (updates.error || available === null) return <p className="text-13 text-fg-muted">{t("widgets.updates.unknown")}</p>;
  const security = updates.security ?? 0;
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <Big>{formatCount(available, t.locale)}</Big>
        <span className="text-13 text-fg-muted">{t("widgets.updates.packages")}</span>
      </div>
      {security > 0 ? <StateLine state="warning">{t("widgets.updates.security", { count: security })}</StateLine> : available === 0 ? <StateLine state="running">{t("widgets.updates.upToDate")}</StateLine> : null}
      {updates.reboot_required === true ? <StateLine state="warning">{t("widgets.updates.reboot")}</StateLine> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Security

/** What the security checks found: the ones that need a look, and how many passed. */
export function SecurityBody() {
  const t = useT();
  const security = useQuery(securityOverviewQuery());
  if (security.data === undefined) return <WidgetPending title={t("widgets.security.title")} failed={security.isError} onRetry={() => void security.refetch()} />;
  const counts = security.data.counts;
  if (counts === null || counts === undefined) return <p className="text-13 text-fg-muted">{t("widgets.security.notChecked")}</p>;
  const open = counts.critical + counts.warning;
  const total = open + counts.passed;
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <Big className={cx(counts.critical > 0 && "flex items-center gap-1.5")}>
          {counts.critical > 0 ? <Glyph state="failed" size={14} /> : counts.warning > 0 ? <Glyph state="warning" size={14} /> : null}
          {formatCount(open, t.locale)}
        </Big>
        <span className="text-13 text-fg-muted">{t("widgets.security.open")}</span>
      </div>
      <p className="text-12 text-fg-muted">{t("widgets.security.passed", { passed: counts.passed, total })}</p>
    </div>
  );
}
