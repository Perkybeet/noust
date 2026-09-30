import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { MetricsRead } from "../../api/queries/metrics";
import { ErrorBlock } from "../../components/page/QueryState";
import { Card } from "../../components/ui/Card";
import { Chart, ChartMessage, ChartSkeleton } from "../../components/ui/Chart";
import { SM_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import type { ChartLimit, ChartMarker } from "../../components/ui/Chart";
import { useT } from "../../i18n";
import { ceilingOf, chartData, prepareRead, useZoomRead } from "./metricsData";
import type { PreparedRead, SeriesSpec } from "./metricsData";
import { rangeSpec } from "./ranges";
import type { MetricRange } from "./ranges";

export interface MetricChartProps {
  title: string;
  range: MetricRange;
  /** The series, each named by the metric it reads from the page's range read. */
  series: readonly SeriesSpec[];
  /** The page's range read, which every chart of the page shares. */
  read: UseQueryResult<MetricsRead>;
  /** That read, split per metric; undefined until it arrives. */
  prepared: PreparedRead | undefined;
  format: (value: number) => string;
  yRange?: readonly [number, number];
  fit?: boolean;
  /** A metric whose ceiling the read carries (total memory): said beside the average. */
  ceilingOf?: string;
  limit?: ChartLimit | null;
  markers?: readonly ChartMarker[];
  height: number;
  /** The page's range control, repeated in the enlarged chart. */
  rangeControl: ReactNode;
  empty?: string;
  couldNotLoad: string;
}

/**
 * One chart of a page's range read, in its card, with the four states of anything loaded: the
 * frame of the chart while it loads, the failure in the same frame, and the chart (which says
 * itself when the window has no reading). Enlarged, it zooms by reading the stretch again.
 */
export function MetricChart({
  title,
  range,
  series,
  read,
  prepared,
  format,
  yRange,
  fit = false,
  ceilingOf: ceilingMetric,
  limit,
  markers,
  height,
  rangeControl,
  empty,
  couldNotLoad,
}: MetricChartProps) {
  const t = useT();
  const wide = useMediaQuery(SM_UP);
  const metrics = series.map((spec) => spec.metric);
  const zoom = useZoomRead(metrics, range);

  let body: ReactNode;
  if (prepared === undefined && read.isError) {
    body = (
      <ChartMessage title={title} height={height}>
        <ErrorBlock compact error={read.error} title={couldNotLoad} onRetry={() => void read.refetch()} retrying={read.isRefetching} />
      </ChartMessage>
    );
  } else if (prepared === undefined) {
    body = <ChartSkeleton title={title} height={height} />;
  } else if (!wide && chartData(prepared, series).series.every((line) => line.values.every((value) => value === null))) {
    // A phone does not scroll past a screen of hatching to learn there is no history: a window
    // with no reading at all is one line, named like the chart it stands for.
    return (
      <Card padding="sm" as="div">
        <div role="group" aria-label={title} className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <p className="text-13 font-medium text-fg">{title}</p>
          <p className="text-13 text-fg-muted">{empty ?? t("common.chart.noReadings")}</p>
        </div>
      </Card>
    );
  } else {
    const data = chartData(prepared, series);
    const zoomed = zoom.read === undefined ? undefined : chartData(prepareRead(zoom.read, t), series);
    const ceiling = ceilingMetric !== undefined ? ceilingOf(prepared, ceilingMetric) : null;
    body = (
      <Chart
        title={title}
        description={t(rangeSpec(range).words)}
        {...data}
        formatValue={format}
        height={height}
        fit={fit}
        ceiling={ceiling}
        {...(yRange !== undefined ? { yRange } : ceiling !== null && !fit ? { yRange: [0, ceiling] as const } : {})}
        {...(limit !== undefined ? { limit } : {})}
        {...(markers !== undefined ? { markers } : {})}
        {...(empty !== undefined ? { empty } : {})}
        rangeSelector={{ value: range, control: rangeControl }}
        zoom={{ value: zoom.zoom, onChange: zoom.setZoom, data: zoomed, loading: zoom.loading }}
      />
    );
  }
  return (
    <Card padding="sm" as="div">
      {body}
    </Card>
  );
}
