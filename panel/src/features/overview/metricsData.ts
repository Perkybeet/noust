/**
 * The metrics every chart page reads: one range read per page (all its series on one grid), the
 * finer read an enlarged chart asks for when it zooms, and the translation of either into what
 * the Chart component draws. Shared by the Overview's machine charts and an application's
 * Metrics tab, so both say the same thing about the same data.
 */

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { metricsReadQuery } from "../../api/queries/metrics";
import type { MetricsRead } from "../../api/queries/metrics";
import type { ChartData, ChartSeries, ChartWindow } from "../../components/ui/Chart";
import { cellWords, resolutionWords } from "../../components/ui/chart/time";
import type { T } from "../../i18n";
import type { MetricRange } from "./ranges";

/** How often each window is read again: about as often as a new cell could appear in it. */
const REFRESH_MS: Readonly<Record<MetricRange, number>> = {
  "1h": 15_000,
  "24h": 60_000,
  "7d": 5 * 60_000,
  "30d": 10 * 60_000,
};

/**
 * Several metrics over the page's range, as one grid. A new range keeps the previous one on
 * screen until it arrives (`isPlaceholderData` says so), so an enlarged chart whose range is
 * changed stays open instead of dropping back to a skeleton; the page says it is reading.
 */
export function useMetricsRead(metrics: readonly string[], range: MetricRange, enabled = true) {
  return useQuery({
    ...metricsReadQuery(metrics, { window: range }),
    refetchInterval: REFRESH_MS[range],
    placeholderData: keepPreviousData,
    enabled: enabled && metrics.length > 0,
  });
}

/**
 * The zoom of an enlarged chart, and the stretch read again at the step it calls for. The zoom
 * belongs to the range it was made in: a new range starts whole.
 */
export function useZoomRead(metrics: readonly string[], range: MetricRange) {
  const [state, setState] = useState<{ range: MetricRange; window: ChartWindow } | null>(null);
  const zoom = state !== null && state.range === range ? state.window : null;
  const read = useQuery({
    ...metricsReadQuery(metrics, zoom === null ? { window: range } : { from: zoom[0], to: zoom[1] }),
    enabled: zoom !== null,
    staleTime: 60_000,
  });
  return {
    zoom,
    setZoom: (window: ChartWindow | null) => {
      setState(window === null ? null : { range, window });
    },
    read: zoom === null ? undefined : read.data,
    loading: zoom !== null && read.isFetching && read.data === undefined,
  };
}

/** One series of a chart, named by the metric it reads. */
export interface SeriesSpec {
  metric: string;
  label: string;
  state?: ChartSeries["state"];
}

/** What a range read gives every chart of a page: the grid, and each metric's values and peaks. */
export interface PreparedRead {
  grid: Omit<ChartData, "series">;
  byMetric: ReadonlyMap<string, { values: (number | null)[]; peaks: (number | null)[]; ceiling: number | null }>;
}

/** Splits a range read into the shared grid and one pair of arrays per metric. */
export function prepareRead(read: MetricsRead, t: T): PreparedRead {
  const byMetric = new Map<string, { values: (number | null)[]; peaks: (number | null)[]; ceiling: number | null }>();
  let timestamps: number[] = [];
  read.series.forEach((series, index) => {
    if (index === 0) timestamps = series.points.map(([at]) => at);
    byMetric.set(series.metric, {
      values: series.points.map(([, mean]) => (mean === null || !Number.isFinite(mean) ? null : mean)),
      peaks: series.points.map(([, , peak]) => (peak === null || !Number.isFinite(peak) ? null : peak)),
      ceiling: series.ceiling ?? null,
    });
  });
  if (timestamps.length === 0) {
    // No series asked for: still the grid the answer describes, so the axis is the window.
    for (let at = read.from; at <= read.to; at += read.step) timestamps.push(at);
  }
  return {
    grid: {
      timestamps,
      domain: [read.from, read.to],
      step: read.step,
      resolution: resolutionWords(t, read.resolution, read.step),
      cell: cellWords(t, read.resolution, read.step),
      firstSampleAt: read.first_sample_at ?? null,
    },
    byMetric,
  };
}

/** The chart data for some series of a prepared read. */
export function chartData(prepared: PreparedRead, specs: readonly SeriesSpec[]): ChartData {
  return {
    ...prepared.grid,
    series: specs.map((spec) => {
      const found = prepared.byMetric.get(spec.metric);
      return {
        label: spec.label,
        values: found?.values ?? prepared.grid.timestamps.map(() => null),
        ...(found ? { peaks: found.peaks } : {}),
        ...(spec.state !== undefined ? { state: spec.state } : {}),
      };
    }),
  };
}

/** The ceiling the read gives a metric (total memory for used memory), or null. */
export function ceilingOf(prepared: PreparedRead, metric: string): number | null {
  return prepared.byMetric.get(metric)?.ceiling ?? null;
}
