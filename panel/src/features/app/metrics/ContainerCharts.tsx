/**
 * A Compose stack's containers, each with the series of its own: CPU, memory, disk and network
 * charted across the stack so the service that reads the disk is the one named. A chart draws up
 * to three lines, so a stack of more containers shows the busiest three of each role over the
 * range and says how many there are.
 */

import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { AppMetricsStatus, MetricsRead } from "../../../api/queries/metrics";
import { Subsection } from "../../../components/page/Subsection";
import type { ChartMarker } from "../../../components/ui/Chart";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatBytesRate, formatPercent } from "../../../lib/format";
import { MetricChart } from "../../overview/MetricChart";
import type { PreparedRead, SeriesSpec } from "../../overview/metricsData";
import type { MetricRange } from "../../overview/ranges";

type Container = NonNullable<AppMetricsStatus["containers"]>[number];

/** The roles a container has, in the order they are charted. */
export const CONTAINER_ROLES = ["cpu", "memory", "disk_read", "disk_write", "net_rx", "net_tx"] as const;
export type ContainerRole = (typeof CONTAINER_ROLES)[number];

/** Most lines one chart draws. */
const MOST_LINES = 3;

/** Every metric the containers have, in a stable order, for the page's one range read. */
export function containerMetrics(containers: readonly Container[]): string[] {
  const names = new Set<string>();
  for (const role of CONTAINER_ROLES) {
    for (const container of containers) {
      const metric = container.series?.[role];
      if (metric !== undefined) names.add(metric);
    }
  }
  return [...names];
}

/** What the containers read on average over the range, for the role: the busiest first. */
export function busiest(prepared: PreparedRead, containers: readonly Container[], role: ContainerRole): { name: string; metric: string; mean: number }[] {
  const ranked: { name: string; metric: string; mean: number }[] = [];
  for (const container of containers) {
    const metric = container.series?.[role];
    if (metric === undefined) continue;
    const values = (prepared.byMetric.get(metric)?.values ?? []).filter((value): value is number => value !== null);
    if (values.length === 0) continue;
    ranked.push({ name: container.name, metric, mean: values.reduce((sum, value) => sum + value, 0) / values.length });
  }
  return ranked.sort((a, b) => b.mean - a.mean || a.name.localeCompare(b.name));
}

function formatterFor(role: ContainerRole, t: T): (value: number) => string {
  if (role === "cpu") return (value) => formatPercent(value, t.locale);
  if (role === "memory") return (value) => formatBytes(value, t.locale);
  return (value) => formatBytesRate(value, t.locale);
}

export interface ContainerChartsProps {
  containers: readonly Container[];
  range: MetricRange;
  read: UseQueryResult<MetricsRead>;
  prepared: PreparedRead | undefined;
  markers: readonly ChartMarker[];
  rangeControl: ReactNode;
  height: number;
  domain: string;
}

/** The per-container charts of a stack of two containers or more; nothing for one, which the main charts already are. */
export function ContainerCharts({ containers, range, read, prepared, markers, rangeControl, height, domain }: ContainerChartsProps) {
  const t = useT();
  if (containers.length < 2) return null;
  const present = CONTAINER_ROLES.filter((role) => containers.some((container) => container.series?.[role] !== undefined));
  if (present.length === 0) return null;
  const shown = Math.min(MOST_LINES, containers.length);
  return (
    <Subsection title={t("appTraffic.containers.title")} description={t("appTraffic.containers.description")} className="mt-2">
      {containers.length > MOST_LINES ? <p className="text-12 text-fg-muted">{t("appTraffic.containers.busiest", { shown, total: containers.length })}</p> : null}
      <div className="grid min-w-0 gap-4 xl:grid-cols-2">
        {present.map((role) => {
          const title = t(`appTraffic.containers.roles.${role}`);
          // Until the read arrives the first three stand in; then the busiest three over the range.
          const entries =
            prepared === undefined
              ? containers.flatMap((container) => {
                  const metric = container.series?.[role];
                  return metric === undefined ? [] : [{ name: container.name, metric }];
                })
              : busiest(prepared, containers, role);
          const series: SeriesSpec[] = entries.slice(0, MOST_LINES).map((entry) => ({ metric: entry.metric, label: entry.name }));
          return (
            <MetricChart
              key={role}
              title={title}
              range={range}
              series={series}
              read={read}
              prepared={prepared}
              format={formatterFor(role, t)}
              markers={markers}
              height={height}
              rangeControl={rangeControl}
              couldNotLoad={t("appPages.metrics.chartLoadError", { title })}
              investigateApp={domain}
            />
          );
        })}
      </div>
    </Subsection>
  );
}
