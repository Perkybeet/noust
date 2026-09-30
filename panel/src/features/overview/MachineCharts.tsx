import { useMemo } from "react";

import { Section } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { ChartGroup } from "../../components/ui/Chart";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes, formatBytesRate, formatPercent } from "../../lib/format";
import { CollectorNotice, TruthLine } from "./HistoryStatus";
import { MetricChart } from "./MetricChart";
import { prepareRead, useMetricsRead } from "./metricsData";
import type { SeriesSpec } from "./metricsData";
import { RANGES } from "./ranges";
import type { MetricRange } from "./ranges";

/** Every metric the machine charts draw, read together: one request, one grid, one crosshair. */
export const MACHINE_METRICS = ["cpu.percent", "mem.used_bytes", "net.rx_bytes_s", "net.tx_bytes_s", "disk.used_bytes"] as const;

const CHART_HEIGHT = 160;

interface MachineChartSpec {
  id: "cpu" | "memory" | "network" | "disk";
  title: string;
  series: readonly SeriesSpec[];
  format: (value: number) => string;
  yRange?: readonly [number, number];
  /** Fit the axis to the readings: a disk that barely moves would be a flat line from zero. */
  fit?: boolean;
  ceilingOf?: string;
}

function machineCharts(t: T): readonly MachineChartSpec[] {
  const locale = t.locale;
  return [
    {
      id: "cpu",
      title: t("overview.machine.cpu"),
      series: [{ metric: "cpu.percent", label: t("overview.machine.cpu") }],
      format: (value) => formatPercent(value, locale),
      yRange: [0, 100],
    },
    {
      id: "memory",
      title: t("overview.machine.memory"),
      series: [{ metric: "mem.used_bytes", label: t("overview.machine.used") }],
      format: (value) => formatBytes(value, locale),
      ceilingOf: "mem.used_bytes",
    },
    {
      id: "network",
      title: t("overview.machine.network"),
      series: [
        { metric: "net.rx_bytes_s", label: t("overview.machine.in") },
        { metric: "net.tx_bytes_s", label: t("overview.machine.out") },
      ],
      format: (value) => formatBytesRate(value, locale),
    },
    {
      id: "disk",
      title: t("overview.machine.disk"),
      series: [{ metric: "disk.used_bytes", label: t("overview.machine.used") }],
      format: (value) => formatBytes(value, locale),
      fit: true,
      ceilingOf: "disk.used_bytes",
    },
  ];
}

export interface MachineChartsProps {
  range: MetricRange;
  onRangeChange: (range: MetricRange) => void;
  className?: string;
}

/** The one range control, on the section and again in an enlarged chart. */
function RangeControl({ range, onRangeChange }: Pick<MachineChartsProps, "range" | "onRangeChange">) {
  const t = useT();
  return (
    <SegmentedControl
      label={t("overview.machine.rangeLabel")}
      options={RANGES.map((spec) => ({ value: spec.value, label: spec.label }))}
      value={range}
      onValueChange={onRangeChange}
    />
  );
}

/**
 * The machine's history: CPU, memory, network and disk over exactly the chosen window, with a
 * sentence that says what is shown, a crosshair shared by the four, and a word when history is
 * not being recorded (and the command that fixes it).
 */
export function MachineCharts({ range, onRangeChange, className }: MachineChartsProps) {
  const t = useT();
  const charts = useMemo(() => machineCharts(t), [t]);
  const read = useMetricsRead(MACHINE_METRICS, range);
  const prepared = useMemo(() => (read.data === undefined ? undefined : prepareRead(read.data, t)), [read.data, t]);
  const control = <RangeControl range={range} onRangeChange={onRangeChange} />;
  return (
    <Section
      title={t("overview.machine.title")}
      description={<TruthLine read={read.data} range={range} reading={read.isPlaceholderData} />}
      actions={control}
      {...(className !== undefined ? { className } : {})}
    >
      <CollectorNotice collector={read.data?.collector} />
      <ChartGroup>
        {/* Sized by the room the page gives it, not the viewport: the sidebar comes and goes.
            Two abreast from a tablet, four only where each still gets a readable width. */}
        <div className="@container min-w-0">
          <div className="grid min-w-0 gap-4 sm:grid-cols-2 @[88rem]:grid-cols-4">
            {charts.map((spec) => (
              <MetricChart
                key={spec.id}
                title={spec.title}
                range={range}
                series={spec.series}
                read={read}
                prepared={prepared}
                format={spec.format}
                {...(spec.yRange !== undefined ? { yRange: spec.yRange } : {})}
                {...(spec.fit !== undefined ? { fit: spec.fit } : {})}
                {...(spec.ceilingOf !== undefined ? { ceilingOf: spec.ceilingOf } : {})}
                height={CHART_HEIGHT}
                rangeControl={control}
                couldNotLoad={t("overview.machine.couldNotLoad")}
              />
            ))}
          </div>
        </div>
      </ChartGroup>
    </Section>
  );
}
