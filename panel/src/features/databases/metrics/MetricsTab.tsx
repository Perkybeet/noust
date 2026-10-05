import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { databaseMetricsQuery, slowQueriesQuery } from "../../../api/queries/databases";
import type { SlowQueries } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { StatTile } from "../../../components/page/StatTile";
import { Card } from "../../../components/ui/Card";
import { ChartGroup, ChartSkeleton } from "../../../components/ui/Chart";
import { DataTable } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { PlainKey, T } from "../../../i18n";
import { formatBytes, formatCount, formatDecimal, formatDuration } from "../../../lib/format";
import { MetricChart } from "../../overview/MetricChart";
import { prepareRead, useMetricsRead } from "../../overview/metricsData";
import { RANGES } from "../../overview/ranges";
import type { MetricRange } from "../../overview/ranges";
import { formatHitRatio } from "../format";

const CHART_HEIGHT = 160;

/** Why an engine keeps no statement statistics, in the console's words. */
const SLOW_REASONS: Readonly<Record<string, { title: PlainKey; body: PlainKey }>> = {
  not_loaded: { title: "databases.metrics.slow.notLoadedTitle", body: "databases.metrics.slow.notLoadedBody" },
  not_created: { title: "databases.metrics.slow.notCreatedTitle", body: "databases.metrics.slow.notCreatedBody" },
  performance_schema_off: { title: "databases.metrics.slow.perfSchemaTitle", body: "databases.metrics.slow.perfSchemaBody" },
  not_supported: { title: "databases.metrics.slow.notSupportedTitle", body: "databases.metrics.slow.notSupportedBody" },
};

type Chart = "size" | "connections" | "cache_hit" | "tps";

function chartTitle(t: T, chart: Chart): string {
  switch (chart) {
    case "size":
      return t("databases.metrics.size");
    case "connections":
      return t("databases.metrics.connections");
    case "cache_hit":
      return t("databases.metrics.cacheHit");
    case "tps":
      return t("databases.metrics.tps");
  }
}

function chartFormat(t: T, chart: Chart): (value: number) => string {
  switch (chart) {
    case "size":
      return (value) => formatBytes(value, t.locale);
    case "connections":
      return (value) => formatCount(Math.round(value), t.locale);
    case "cache_hit":
      return (value) => formatHitRatio(value, t.locale);
    case "tps":
      return (value) => formatDecimal(value, t.locale);
  }
}

function SlowQueriesView({ answer, t }: { answer: SlowQueries; t: T }) {
  if (!answer.available) {
    const words = SLOW_REASONS[answer.reason ?? ""] ?? SLOW_REASONS["not_supported"];
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <Notice title={t(words?.title ?? "databases.metrics.slow.notSupportedTitle")}>{t(words?.body ?? "databases.metrics.slow.notSupportedBody")}</Notice>
        {answer.how_to_enable ? (
          <SystemOutput label={t("databases.metrics.slow.steps")} maxHeight="max-h-72">
            {answer.how_to_enable}
          </SystemOutput>
        ) : null}
      </div>
    );
  }
  return (
    <DataTable
      caption={t("databases.metrics.slow.caption")}
      density="compact"
      rows={answer.queries ?? []}
      getRowId={(query) => query.query}
      empty={<EmptyState variant="inline" title={t("databases.metrics.slow.none")} />}
      columns={[
        {
          id: "query",
          header: t("databases.metrics.slow.statement"),
          cell: (query) => (
            <Mono truncate className="max-w-xl">
              {query.query}
            </Mono>
          ),
        },
        {
          id: "mean",
          header: t("databases.metrics.slow.mean"),
          align: "end",
          mono: true,
          width: "w-28",
          sortValue: (query) => query.mean_ms ?? null,
          cell: (query) => (query.mean_ms != null ? t("databases.metrics.slow.ms", { ms: formatDecimal(query.mean_ms, t.locale) }) : <EmptyCell reason={t("databases.metrics.slow.noValue")} />),
        },
        {
          id: "calls",
          header: t("databases.metrics.slow.calls"),
          align: "end",
          mono: true,
          width: "w-24",
          sortValue: (query) => query.calls ?? null,
          cell: (query) => (query.calls != null ? formatCount(query.calls, t.locale) : <EmptyCell reason={t("databases.metrics.slow.noValue")} />),
        },
        {
          id: "total",
          header: t("databases.metrics.slow.total"),
          align: "end",
          mono: true,
          width: "w-28",
          hideBelow: "md",
          sortValue: (query) => query.total_ms ?? null,
          cell: (query) => (query.total_ms != null ? formatDuration(query.total_ms / 1000, t.locale) : <EmptyCell reason={t("databases.metrics.slow.noValue")} />),
        },
      ]}
    />
  );
}

export interface MetricsTabProps {
  engine: string;
  name: string;
  range: MetricRange;
  onRangeChange: (range: MetricRange) => void;
}

/**
 * How a database is doing: its size, connections, cache hits and transactions now and over the
 * page's range, its biggest tables, and the statements that take longest on average (or what
 * turns their statistics on, and what that costs).
 */
export function MetricsTab({ engine, name, range, onRangeChange }: MetricsTabProps) {
  const t = useT();
  const metrics = useQuery({ ...databaseMetricsQuery(engine, name), refetchInterval: 60_000 });
  const slow = useQuery(slowQueriesQuery(engine, name));
  const series = metrics.data?.series ?? {};
  const charts = (["size", "connections", "cache_hit", "tps"] as const).filter((chart) => series[chart] !== undefined);
  const metricNames = charts.map((chart) => series[chart] ?? "");
  const read = useMetricsRead(metricNames, range, metricNames.length > 0);
  const prepared = useMemo(() => (read.data === undefined ? undefined : prepareRead(read.data, t)), [read.data, t]);
  const data = metrics.data;
  const control = (
    <SegmentedControl label={t("databases.metrics.rangeLabel")} options={RANGES.map((spec) => ({ value: spec.value, label: spec.label }))} value={range} onValueChange={onRangeChange} />
  );

  if (metrics.isError && data === undefined) {
    return <ErrorBlock error={metrics.error} title={t("databases.metrics.couldNotLoad")} onRetry={() => void metrics.refetch()} retrying={metrics.isRefetching} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {data === undefined ? (
          // Tiles of the real shape, so the charts below do not move when the readings arrive.
          (["size", "connections", "cacheHit", "transactions"] as const).map((tile) => (
            <StatTile key={tile} loading label={t(`databases.metrics.${tile}`)} value={<Skeleton className="h-5 w-16" />} detail={<Skeleton className="h-3 w-24" />} />
          ))
        ) : (
          <>
            <StatTile label={t("databases.metrics.size")} value={data.size_bytes != null ? formatBytes(data.size_bytes, t.locale) : "–"} detail={t("databases.metrics.onDisk")} />
            <StatTile
              label={t("databases.metrics.connections")}
              value={data.connections != null ? formatCount(data.connections, t.locale) : "–"}
              detail={
                data.max_connections != null
                  ? t("databases.metrics.ofServer", { server: formatCount(data.server_connections ?? 0, t.locale), max: formatCount(data.max_connections, t.locale) })
                  : t("databases.metrics.toThisDatabase")
              }
            />
            <StatTile label={t("databases.metrics.cacheHit")} value={data.cache_hit_ratio != null ? formatHitRatio(data.cache_hit_ratio, t.locale) : "–"} detail={t("databases.metrics.cacheHitDetail")} />
            {data.keys != null ? (
              <StatTile label={t("databases.metrics.keys")} value={formatCount(data.keys, t.locale)} detail={t("databases.metrics.inSlot")} />
            ) : (
              <StatTile
                label={t("databases.metrics.transactions")}
                value={data.transactions != null ? formatCount(data.transactions, t.locale) : "–"}
                detail={data.deadlocks != null ? t("databases.metrics.deadlocks", { count: data.deadlocks }) : t("databases.metrics.sinceStart")}
              />
            )}
          </>
        )}
      </div>

      {data === undefined ? (
        // The charts' room while the engine is asked which it has: the four a SQL engine draws.
        <Section title={t("databases.metrics.history")} actions={control}>
          <div className="grid gap-4 lg:grid-cols-2">
            {(["size", "connections", "cache_hit", "tps"] as const).map((chart) => (
              <Card key={chart} padding="sm" as="div">
                <ChartSkeleton title={chartTitle(t, chart)} height={CHART_HEIGHT} />
              </Card>
            ))}
          </div>
        </Section>
      ) : charts.length > 0 ? (
        <Section title={t("databases.metrics.history")} actions={control}>
          <ChartGroup>
            <div className="grid gap-4 lg:grid-cols-2">
              {charts.map((chart) => (
                <MetricChart
                  key={chart}
                  title={chartTitle(t, chart)}
                  range={range}
                  series={[{ metric: series[chart] ?? "", label: chartTitle(t, chart) }]}
                  read={read}
                  prepared={prepared}
                  format={chartFormat(t, chart)}
                  {...(chart === "cache_hit" ? { yRange: [0, 100] as const } : {})}
                  fit={chart === "size"}
                  height={CHART_HEIGHT}
                  rangeControl={control}
                  couldNotLoad={t("databases.metrics.couldNotLoad")}
                  empty={t("databases.metrics.noHistory")}
                />
              ))}
            </div>
          </ChartGroup>
        </Section>
      ) : null}

      {data !== undefined && (data.tables ?? []).length > 0 ? (
        <Section title={t("databases.metrics.biggest")}>
          <DataTable
            caption={t("databases.metrics.biggestCaption", { name })}
            density="compact"
            rows={data.tables ?? []}
            getRowId={(table) => `${table.schema}.${table.name}`}
            defaultSort={{ column: "size", direction: "descending" }}
            columns={[
              { id: "name", header: t("databases.metrics.table"), cell: (table) => <Mono>{`${table.schema}.${table.name}`}</Mono> },
              {
                id: "rows",
                header: t("databases.metrics.rowsEstimate"),
                align: "end",
                mono: true,
                width: "w-32",
                sortValue: (table) => table.rows_estimate ?? null,
                cell: (table) => (table.rows_estimate != null ? formatCount(table.rows_estimate, t.locale) : <EmptyCell reason={t("databases.metrics.noEstimate")} />),
              },
              {
                id: "size",
                header: t("databases.metrics.size"),
                align: "end",
                mono: true,
                width: "w-32",
                sortValue: (table) => table.size_bytes ?? null,
                cell: (table) => (table.size_bytes != null ? formatBytes(table.size_bytes, t.locale) : <EmptyCell reason={t("databases.metrics.noEstimate")} />),
              },
            ]}
          />
        </Section>
      ) : null}

      <Section title={t("databases.metrics.slow.title")} description={t("databases.metrics.slow.description")} loading={slow.data === undefined && !slow.isError}>
        {slow.isError ? (
          <ErrorBlock compact error={slow.error} title={t("databases.metrics.slow.failed")} onRetry={() => void slow.refetch()} retrying={slow.isRefetching} />
        ) : slow.data === undefined ? (
          <Skeleton className="h-32 w-full rounded-card" />
        ) : (
          <SlowQueriesView answer={slow.data} t={t} />
        )}
      </Section>

      <CommandHint command={`noust db metrics ${engine} ${name}`} label={t("databases.common.fromTerminal")} />
    </div>
  );
}
