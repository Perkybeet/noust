/**
 * One metric of every server over the same window, side by side: which server is the busiest, and
 * how each has been over the last hour, day, week or month. Each server answers from its own recorded
 * history (the central keeps none), memory, swap and disk are the share of what each server has so a
 * small server and a large one compare, and a server that is down is a gap with its own words.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { COMPARE_METRICS, COMPARE_WINDOWS, fleetMetricsQuery } from "../../api/queries/fleetCompare";
import type { CompareMetric, CompareWindow } from "../../api/queries/fleetCompare";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Card } from "../../components/ui/Card";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytesRate, formatPercent } from "../../lib/format";
import { numberOf, originOf, textOf } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { ServerCell } from "./parts";

/** A reading in the unit the server said it is in. */
export function readingText(value: number | null, unit: string | null, locale: Parameters<typeof formatPercent>[1]): string | null {
  if (value === null) return null;
  if (unit === "percent") return formatPercent(value, locale);
  if (unit === "bytes_s") return formatBytesRate(value, locale);
  return String(Math.round(value * 100) / 100);
}

function reading(t: T, row: FleetRow, key: "latest" | "average" | "peak") {
  const text = readingText(numberOf(row[key]), textOf(row["unit"]), t.locale);
  return text === null ? <EmptyCell reason={t("fleetCompare.metrics.noReading")} /> : <span className="text-13 text-fg tabular-nums">{text}</span>;
}

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<FleetRow>[] {
  return [
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "title",
      sortValue: (row) => originOf(row).node,
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
    {
      id: "latest",
      header: t("fleetCompare.metrics.latest"),
      align: "end",
      card: "meta",
      sortValue: (row) => numberOf(row["latest"]),
      cell: (row) => reading(t, row, "latest"),
    },
    {
      id: "average",
      header: t("fleetCompare.metrics.average"),
      align: "end",
      hideBelow: "md",
      card: "meta",
      sortValue: (row) => numberOf(row["average"]),
      cell: (row) => reading(t, row, "average"),
    },
    {
      id: "peak",
      header: t("fleetCompare.metrics.peak"),
      align: "end",
      hideBelow: "md",
      card: "meta",
      sortValue: (row) => numberOf(row["peak"]),
      cell: (row) => reading(t, row, "peak"),
    },
  ];
}

/** The comparison card of the fleet's Summary: the metric and the window chosen here, the servers below. */
export function FleetMetricsCard() {
  const t = useT();
  const [metric, setMetric] = useState<CompareMetric>("cpu");
  const [window, setWindow] = useState<CompareWindow>("24h");
  const query = useQuery(fleetMetricsQuery(metric, window));
  const outcomes = new Map((query.data?.nodes ?? []).map((outcome) => [outcome.name, outcome]));
  // A server that did not answer is a gap in the comparison, said once; the Summary above names why.
  const gaps = (query.data?.nodes ?? []).filter((outcome) => outcome.status !== "ok").length;
  // Busiest first: the question is which server is under the most load.
  const rows = [...(query.data?.items ?? [])].sort((a, b) => (numberOf(b["latest"]) ?? -1) - (numberOf(a["latest"]) ?? -1));
  return (
    <Card
      as="section"
      level={2}
      padding="none"
      title={t("fleetCompare.metrics.title")}
      description={t("fleetCompare.metrics.description")}
      actions={
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Select<CompareMetric>
            aria-label={t("fleetCompare.metrics.metricLabel")}
            value={metric}
            onValueChange={setMetric}
            options={COMPARE_METRICS.map((value) => ({ value, label: t(`fleetCompare.metrics.metric.${value}`) }))}
          />
          <SegmentedControl<CompareWindow>
            label={t("fleetCompare.metrics.windowLabel")}
            value={window}
            onValueChange={setWindow}
            options={COMPARE_WINDOWS.map((value) => ({ value, label: t(`fleetCompare.metrics.window.${value}`) }))}
          />
        </div>
      }
    >
      {/* design-exception: hand-error-ternary the table draws its own skeleton rows inside the card's frame */}
      {query.isError && query.data === undefined ? (
        <ErrorBlock compact error={query.error} title={t("fleetCompare.metrics.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} className="m-4" />
      ) : (
        <div className="flex min-w-0 flex-col gap-3">
          {gaps > 0 ? <p className="px-4 text-12 text-fg-muted">{t("fleetCompare.metrics.gaps", { count: gaps })}</p> : null}
          <DataTable
            caption={t("fleetCompare.metrics.caption", { metric: t(`fleetCompare.metrics.metric.${metric}`), window: t(`fleetCompare.metrics.window.${window}`) })}
            columns={columns(t, outcomes)}
            rows={rows}
            getRowId={(row) => originOf(row).node}
            loading={query.isPending}
            skeletonRows={3}
            empty={<EmptyState variant="inline" title={t("fleetCompare.metrics.none")} />}
            className="rounded-none border-0 shadow-none"
          />
        </div>
      )}
    </Card>
  );
}
