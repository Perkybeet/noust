import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChartLine } from "lucide-react";
import type { ReactNode } from "react";

import { appQuery } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { deploymentsQuery } from "../../../api/queries/deployments";
import { metricSeriesQuery } from "../../../api/queries/metrics";
import { useDocumentTitle } from "../../../app/documentTitle";
import { useNow } from "../../../components/page/clock";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { appStatus, deployStatus } from "../../../components/page/status";
import { Chart } from "../../../components/ui/Chart";
import type { ChartMarker } from "../../../components/ui/Chart";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Skeleton } from "../../../components/ui/Skeleton";
import { STATUS, StatusGlyph } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T, PlainKey } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatBytes, formatPercent, parseTimestamp } from "../../../lib/format";
import { appLimits } from "../../apps/data";
import { alignSeries, resolutionCategory } from "../../overview/series";
import { RANGES, clip, momentWords, rangeSpec, rangeWords, sentence, summarise } from "./ranges";
import type { MetricRange, Points } from "./ranges";

const CHART_HEIGHT = 180;

const TONE_TEXT = { ok: "text-ok", warn: "text-warn", fail: "text-fail", idle: "text-idle" } as const;

/** One deploy in the range: what the chart's markers and the list below the charts both say. */
interface DeployMark {
  id: number;
  /** Unix seconds. */
  at: number;
  status: string;
  /** When, as short as the range allows: "19:42", "Sep 25, 19:42". */
  when: string;
  /** Everything, for assistive technology and the tooltip: "Deploy 25, succeeded, Sep 25, 19:42". */
  label: string;
}

interface ChartSpec {
  key: "cpu" | "memory";
  title: string;
  metric: (domain: string) => string;
  format: (value: number, locale: T["locale"]) => string;
  limit: (app: App) => number | null;
  /** What the value is, beside the title. */
  unit: string;
}

function charts(t: T): readonly ChartSpec[] {
  return [
    {
      key: "cpu",
      title: t("appPages.common.cpu"),
      metric: (domain) => `app.${domain}.cpu.percent`,
      format: formatPercent,
      limit: (app) => appLimits(app).cpu,
      unit: t("appPages.metrics.unitCpu"),
    },
    {
      key: "memory",
      title: t("appPages.common.memory"),
      metric: (domain) => `app.${domain}.mem.bytes`,
      format: formatBytes,
      limit: (app) => appLimits(app).memory,
      unit: t("appPages.metrics.unitMemory"),
    },
  ];
}

function Frame({ children }: { children: ReactNode }) {
  return <div className="min-w-0 rounded-card border border-border bg-surface p-4 shadow-raised">{children}</div>;
}

/**
 * Shaped like the chart that replaces it, block for block: the caption (36px), the readout
 * (16px), the plot, and the summary sentence under its rule. The page below stays put when
 * the data lands.
 */
function ChartSkeleton({ title, t }: { title: string; t: T }) {
  return (
    <Frame>
      <div aria-busy="true">
        <span className="sr-only">{t("appPages.metrics.loadingChart", { title })}</span>
        <div aria-hidden="true" className="flex flex-col gap-3">
          <div className="flex h-9 flex-col justify-center gap-1.5">
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="h-3 w-64 max-w-full" />
          </div>
          <div className="flex h-4 items-center">
            <Skeleton className="h-3 w-32" />
          </div>
          <Skeleton className="h-45 w-full rounded-control" />
          <div className="flex h-7 items-end border-t border-border">
            <Skeleton className="h-3 w-80 max-w-full" />
          </div>
        </div>
      </div>
    </Frame>
  );
}

/** One metric of the app over the range, with its limit as a second line and its deploys marked. */
function MetricChart({
  spec,
  app,
  range,
  markers,
  now,
  onRangeChange,
  t,
}: {
  spec: ChartSpec;
  app: App;
  range: MetricRange;
  markers: readonly ChartMarker[];
  now: number;
  onRangeChange: (range: MetricRange) => void;
  t: T;
}) {
  const detail = rangeSpec(range);
  const words = rangeWords(range, t.locale);
  const series = useQuery({
    ...metricSeriesQuery(spec.metric(app.domain), detail.window),
    refetchInterval: range === "1h" ? 30_000 : 5 * 60_000,
    // A new range keeps the previous one on screen until it arrives, so an enlarged chart
    // whose range is changed stays open instead of dropping back to a skeleton.
    placeholderData: keepPreviousData,
  });

  if (series.isError && series.data === undefined) {
    return (
      <Frame>
        <ErrorBlock compact error={series.error} title={t("appPages.metrics.chartLoadError", { title: spec.title })} onRetry={() => void series.refetch()} />
      </Frame>
    );
  }
  if (series.data === undefined) return <ChartSkeleton title={spec.title} t={t} />;

  const points: Points = clip(series.data.points, range, now);
  const summary = summarise(points);
  const limit = spec.limit(app);
  if (summary === null) {
    return (
      <Frame>
        <div className="flex flex-col gap-1">
          <h3 className="text-13 font-medium text-fg">{spec.title}</h3>
          <p className="text-13 text-fg-muted">{t(NO_READINGS[range])}</p>
        </div>
      </Frame>
    );
  }

  const aligned = alignSeries([points]);
  const values = aligned.values[0] ?? [];
  // A limit far above the readings would flatten them against the axis; drawn only when the
  // app comes near it, and always said in the summary.
  const drawLimit = limit !== null && summary.peak >= limit / 3;
  const lines = [
    { label: spec.title, values },
    ...(drawLimit ? [{ label: t("appPages.metrics.limitLabel"), values: values.map(() => limit) }] : []),
  ];

  const format = (value: number) => spec.format(value, t.locale);
  const category = resolutionCategory(series.data.resolution);
  const spacing = category === "minute" ? t("appPages.metrics.minuteAverages") : category === "hour" ? t("appPages.metrics.hourlyAverages") : null;

  return (
    <Frame>
      <div className="flex flex-col gap-3">
        <Chart
          title={spec.title}
          description={`${[words, spacing].filter((part) => part !== null).join(", ")}. ${spec.unit}.`}
          timestamps={aligned.timestamps}
          series={lines}
          formatValue={format}
          height={CHART_HEIGHT}
          markers={markers}
          rangeSelector={{ value: range, control: <RangeControl range={range} onRangeChange={onRangeChange} t={t} /> }}
        />
        <p className="border-t border-border pt-3 text-12 text-pretty text-fg-muted" data-summary="">
          <span className="sr-only">{`${spec.title}: `}</span>
          {sentence(summary, range, format, limit, t.locale)}
        </p>
      </div>
    </Frame>
  );
}

/** The deploys in the range, in words: what the marks on the charts are. */
function DeployList({ domain, marks, t }: { domain: string; marks: readonly DeployMark[]; t: T }) {
  return (
    <Section title={t("appPages.metrics.deploysInRangeTitle")} level={3}>
      {marks.length === 0 ? (
        <p className="text-13 text-fg-muted">{t("appPages.metrics.noDeploysInRange")}</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {marks.map((mark) => {
            const view = deployStatus(mark.status);
            return (
              <li key={mark.id}>
                <Link
                  to="/apps/$domain/deployments/$id"
                  params={{ domain, id: String(mark.id) }}
                  className="flex h-7 items-center gap-1.5 rounded-pill border border-border bg-surface px-2.5 text-12 text-fg hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-focus"
                >
                  <span className={cx("flex", TONE_TEXT[STATUS[view.state].tone])}>
                    <StatusGlyph state={view.state} size={10} />
                  </span>
                  <span className="sr-only">{mark.label}</span>
                  <span aria-hidden="true" className="flex items-baseline gap-1.5">
                    <span className="mono">{`#${String(mark.id)}`}</span>
                    <span className="text-fg-muted">{mark.when}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}


/** A sentence per range: "in the last hour" is not a lowercased "Last hour" in every language. */
const NO_READINGS: Readonly<Record<MetricRange, PlainKey>> = {
  "1h": "appPages.metrics.noReadings.1h",
  "24h": "appPages.metrics.noReadings.24h",
  "7d": "appPages.metrics.noReadings.7d",
  "30d": "appPages.metrics.noReadings.30d",
};

export interface MetricsTabProps {
  domain: string;
  range: MetricRange;
  onRangeChange: (range: MetricRange) => void;
}

/** The one range control, on the section and again in an enlarged chart. */
function RangeControl({ range, onRangeChange, t }: Pick<MetricsTabProps, "range" | "onRangeChange"> & { t: T }) {
  return (
    <SegmentedControl
      label={t("appPages.metrics.timeRangeLabel")}
      options={RANGES.map((spec) => ({ value: spec.value, label: spec.label }))}
      value={range}
      onValueChange={onRangeChange}
    />
  );
}

/**
 * The app's CPU and memory over the last hour, day, week or month, against its limits, with its
 * deploys marked. Each chart has a sentence that says what it shows and a table of its numbers.
 */
export function MetricsTab({ domain, range, onRangeChange }: MetricsTabProps) {
  const t = useT();
  useDocumentTitle(t("appPages.metrics.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));
  const deploys = useQuery(deploymentsQuery({ domain, limit: 200 }));
  // One clock for both charts and the marks: the range ends at the same instant for all.
  const now = Math.floor(useNow(() => 60_000) / 1000);

  if (app.data === undefined) {
    return app.isError ? null : (
      <div className="grid gap-4 xl:grid-cols-2">
        <ChartSkeleton title={t("appPages.common.cpu")} t={t} />
        <ChartSkeleton title={t("appPages.common.memory")} t={t} />
      </div>
    );
  }

  // A site of the static type has no process even when a unit was left behind for it.
  if (appStatus(app.data.status).state === "static" || app.data.app_type === "static") {
    return (
      <EmptyState
        level={2}
        icon={<ChartLine />}
        title={t("appPages.metrics.staticTitle")}
        description={t("appPages.metrics.staticDescription")}
        action={
          <Link to="/" className="rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus">
            {t("appPages.metrics.machineOverview")}
          </Link>
        }
        className="py-16"
      />
    );
  }

  // A Compose stack's unit only starts it; the containers live in Docker's own cgroups,
  // which Noust does not sample, so charts here would read near zero and mislead.
  if (app.data.app_type === "docker-compose") {
    return (
      <EmptyState
        level={2}
        icon={<ChartLine />}
        title={t("appPages.metrics.dockerTitle")}
        description={t("appPages.metrics.dockerDescription")}
        action={
          <Link to="/" className="rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus">
            {t("appPages.metrics.machineOverview")}
          </Link>
        }
        className="py-16"
      />
    );
  }

  const from = now - rangeSpec(range).seconds;
  const marks: DeployMark[] = (deploys.data?.items ?? []).flatMap((deploy) => {
    const at = parseTimestamp(deploy.started_at);
    if (at === null) return [];
    const seconds = Math.floor(at.getTime() / 1000);
    if (seconds <= from || seconds > now) return [];
    const status = deployStatus(deploy.status, t.locale).label.toLowerCase();
    const when = momentWords(seconds, range, t.locale);
    return [{ id: deploy.id, at: seconds, status: deploy.status, when, label: t("appPages.metrics.deployMarkLabel", { id: String(deploy.id), status, when }) }];
  });
  marks.sort((a, b) => a.at - b.at);

  // The chart draws each mark itself: a hairline and a focusable state glyph linking to the
  // deploy. Chart does not import the router, so the link is built here and handed in.
  const chartMarkers: ChartMarker[] = marks.map((mark) => ({
    at: mark.at,
    label: mark.label,
    state: deployStatus(mark.status).state,
    renderMarker: (marker, children, linkProps) => (
      <Link
        to="/apps/$domain/deployments/$id"
        params={{ domain, id: String(mark.id) }}
        aria-label={marker.label}
        className={linkProps.className}
        style={linkProps.style}
      >
        {children}
      </Link>
    ),
  }));

  return (
    <Section
      title={t("appPages.metrics.mainTitle")}
      description={t("appPages.metrics.mainDescription")}
      actions={<RangeControl range={range} onRangeChange={onRangeChange} t={t} />}
    >
      <div className="grid min-w-0 gap-4 xl:grid-cols-2">
        {charts(t).map((spec) => (
          <MetricChart
            key={spec.key}
            spec={spec}
            app={app.data}
            range={range}
            markers={chartMarkers}
            now={now}
            onRangeChange={onRangeChange}
            t={t}
          />
        ))}
      </div>
      <DeployList domain={domain} marks={marks} t={t} />
    </Section>
  );
}
