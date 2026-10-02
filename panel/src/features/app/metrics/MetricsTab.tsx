import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChartLine } from "lucide-react";
import { useMemo } from "react";

import { appQuery } from "../../../api/queries/apps";
import { deploymentsQuery } from "../../../api/queries/deployments";
import { appMetricsStatusQuery } from "../../../api/queries/metrics";
import type { AppMetricsStatus, MetricsReason } from "../../../api/queries/metrics";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { deployStatus } from "../../../components/page/status";
import { Subsection } from "../../../components/page/Subsection";
import { Card } from "../../../components/ui/Card";
import { ChartGroup, ChartSkeleton } from "../../../components/ui/Chart";
import type { ChartMarker } from "../../../components/ui/Chart";
import { formatChartTime, momentNeedsDate } from "../../../components/ui/chart/time";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { StatusGlyph, stateTextClass } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { PlainKey, T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatBytes, formatCount, formatPercent, parseTimestamp } from "../../../lib/format";
import { appLimits } from "../../apps/data";
import { CollectorNotice, TruthLine, collectorReasonText } from "../../overview/HistoryStatus";
import { MetricChart } from "../../overview/MetricChart";
import { prepareRead, useMetricsRead } from "../../overview/metricsData";
import type { SeriesSpec } from "../../overview/metricsData";
import { RANGES } from "../../overview/ranges";
import type { MetricRange } from "../../overview/ranges";

const CHART_HEIGHT = 180;

interface ReasonKeys {
  title: PlainKey;
  description: PlainKey;
  /** What to do, leading into the command when there is one. */
  fix?: PlainKey;
}

/** Every reason the backend gives, with the console's words for it. */
const REASONS: Readonly<Record<string, ReasonKeys>> = {
  static: { title: "appPages.metrics.reason.static.title", description: "appPages.metrics.reason.static.description" },
  php_fpm_missing: { title: "appPages.metrics.reason.php_fpm_missing.title", description: "appPages.metrics.reason.php_fpm_missing.description", fix: "appPages.metrics.reason.php_fpm_missing.fix" },
  stopped: { title: "appPages.metrics.reason.stopped.title", description: "appPages.metrics.reason.stopped.description", fix: "appPages.metrics.reason.stopped.fix" },
  failed: { title: "appPages.metrics.reason.failed.title", description: "appPages.metrics.reason.failed.description", fix: "appPages.metrics.reason.failed.fix" },
  starting: { title: "appPages.metrics.reason.starting.title", description: "appPages.metrics.reason.starting.description" },
  unit_missing: { title: "appPages.metrics.reason.unit_missing.title", description: "appPages.metrics.reason.unit_missing.description", fix: "appPages.metrics.reason.unit_missing.fix" },
  cgroup_v1: { title: "appPages.metrics.reason.cgroup_v1.title", description: "appPages.metrics.reason.cgroup_v1.description", fix: "appPages.metrics.reason.cgroup_v1.fix" },
  accounting_off: { title: "appPages.metrics.reason.accounting_off.title", description: "appPages.metrics.reason.accounting_off.description", fix: "appPages.metrics.reason.accounting_off.fix" },
  cgroup_missing: { title: "appPages.metrics.reason.cgroup_missing.title", description: "appPages.metrics.reason.cgroup_missing.description", fix: "appPages.metrics.reason.cgroup_missing.fix" },
  compose_docker_unavailable: { title: "appPages.metrics.reason.compose_docker_unavailable.title", description: "appPages.metrics.reason.compose_docker_unavailable.description", fix: "appPages.metrics.reason.compose_docker_unavailable.fix" },
  compose_cgroup_unreadable: { title: "appPages.metrics.reason.compose_cgroup_unreadable.title", description: "appPages.metrics.reason.compose_cgroup_unreadable.description", fix: "appPages.metrics.reason.compose_cgroup_unreadable.fix" },
  access_log_missing: { title: "appPages.metrics.reason.access_log_missing.title", description: "appPages.metrics.reason.access_log_missing.description", fix: "appPages.metrics.reason.access_log_missing.fix" },
};

/** A reason in the console's words; one it does not know keeps Noust's own sentence. */
export function reasonWords(t: T, reason: Pick<MetricsReason, "code" | "message">): { title: string; description: string } {
  const known = REASONS[reason.code];
  if (known) return { title: t(known.title), description: t(known.description) };
  return { title: t("appPages.metrics.reason.other.title"), description: `${t("appPages.metrics.reason.other.description")} ${reason.message}` };
}

const COMMAND = /^(?:noust|systemctl|journalctl|docker)\s[^\s]/;

/**
 * The command that fixes a reason, verbatim: the backend's fix when it is one, the command at
 * the end of its sentence ("Redeploy it: noust app update example.com"), or the one the reason
 * itself names (the service to edit, Docker's own view).
 */
export function fixCommand(reason: Pick<MetricsReason, "code" | "fix" | "params">): string | null {
  const unit = reason.params?.["unit"];
  if (reason.code === "accounting_off" && typeof unit === "string" && unit !== "") return `systemctl edit ${unit}`;
  if (reason.code === "compose_cgroup_unreadable") return "docker stats";
  const fix = reason.fix?.trim() ?? "";
  if (COMMAND.test(fix) && !fix.endsWith(".")) return fix;
  const tail = /:\s+((?:noust|systemctl|journalctl|docker)\s[^()]+?)\.?$/.exec(fix);
  return tail?.[1] ?? null;
}

/**
 * How to fix a reason: the console's words leading into the command (verbatim, copyable), and
 * the system's own words when it said something.
 */
function ReasonDetail({ t, reason }: { t: T; reason: MetricsReason }) {
  const known = REASONS[reason.code];
  const command = fixCommand(reason);
  const lead = known?.fix !== undefined ? t(known.fix) : command !== null ? t("appPages.metrics.fixLabel") : (reason.fix ?? null);
  if (lead === null && command === null && !reason.evidence) return null;
  return (
    <div className="flex min-w-0 flex-col items-start gap-2 text-left">
      {command !== null ? (
        <CommandHint {...(lead !== null ? { label: lead } : {})} command={command} />
      ) : lead !== null ? (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{lead}</p>
      ) : null}
      {reason.evidence ? <SystemOutput label={t("appPages.metrics.evidenceLabel")}>{reason.evidence}</SystemOutput> : null}
    </div>
  );
}

/** One deploy in the range: what the chart's markers and the list below the charts both say. */
interface DeployMark {
  id: number;
  at: number;
  status: string;
  when: string;
  label: string;
}

function DeployList({ domain, marks, t }: { domain: string; marks: readonly DeployMark[]; t: T }) {
  return (
    <Subsection title={t("appPages.metrics.deploysInRangeTitle")}>
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
                  aria-label={mark.label}
                  className="flex h-7 items-center gap-1.5 rounded-pill border border-border bg-surface px-2.5 text-12 text-fg hover:bg-surface-hover"
                >
                  <span className={cx("flex", stateTextClass(view.state))}>
                    <StatusGlyph state={view.state} size={10} />
                  </span>
                  <span aria-hidden="true" className="flex items-baseline gap-1.5">
                    <Mono>{`#${String(mark.id)}`}</Mono>
                    <span className="text-fg-muted">{mark.when}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Subsection>
  );
}

export interface MetricsTabProps {
  domain: string;
  range: MetricRange;
  onRangeChange: (range: MetricRange) => void;
}

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

/** The roles this kind of application has charts for, in the order they are drawn. */
function rolesOf(status: AppMetricsStatus | undefined): { unit: string[]; traffic: string[] } {
  const series: Record<string, string> = status?.series ?? {};
  return {
    unit: ["cpu", "memory"].filter((role) => series[role] !== undefined),
    traffic: ["requests", "errors_5xx"].filter((role) => series[role] !== undefined),
  };
}

/**
 * An application's CPU and memory (or, for a static site, its requests and server errors) over
 * the page's range, against its limits, with its deploys marked; and when nothing is measured,
 * why, and what fixes it.
 */
export function MetricsTab({ domain, range, onRangeChange }: MetricsTabProps) {
  const t = useT();
  useDocumentTitle(t("appPages.metrics.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));
  const status = useQuery(appMetricsStatusQuery(domain));
  const deploys = useQuery(deploymentsQuery({ domain, limit: 200 }));

  const roles = rolesOf(status.data);
  const traffic = roles.unit.length === 0 && roles.traffic.length > 0;
  const shownRoles = traffic ? roles.traffic : roles.unit;
  const metrics = shownRoles.map((role) => status.data?.series?.[role] ?? "");
  const read = useMetricsRead(metrics, range, status.data !== undefined);
  const prepared = useMemo(() => (read.data === undefined ? undefined : prepareRead(read.data, t)), [read.data, t]);
  const control = <RangeControl range={range} onRangeChange={onRangeChange} t={t} />;

  const hasReadings = prepared !== undefined && [...prepared.byMetric.values()].some((series) => series.values.some((value) => value !== null));
  const reason: MetricsReason | null = (traffic ? status.data?.traffic.reason : status.data?.reason) ?? null;
  const collector = read.data?.collector ?? status.data?.collector;

  const marks = useMemo((): DeployMark[] => {
    if (read.data === undefined) return [];
    const window: [number, number] = [read.data.from, read.data.to];
    const withDate = momentNeedsDate(window);
    return (deploys.data?.items ?? [])
      .flatMap((deploy) => {
        const at = parseTimestamp(deploy.started_at);
        if (at === null) return [];
        const seconds = Math.floor(at.getTime() / 1000);
        if (seconds < window[0] || seconds > window[1]) return [];
        const when = formatChartTime(seconds, withDate, t.locale);
        const state = deployStatus(deploy.status, t.locale).label.toLowerCase();
        return [{ id: deploy.id, at: seconds, status: deploy.status, when, label: t("appPages.metrics.deployMarkLabel", { id: String(deploy.id), status: state, when }) }];
      })
      .sort((a, b) => a.at - b.at);
  }, [deploys.data, read.data, t]);

  // The chart draws each mark itself; Chart does not import the router, so the link is built here.
  const markers: ChartMarker[] = marks.map((mark) => ({
    at: mark.at,
    label: mark.label,
    state: deployStatus(mark.status).state,
    renderMarker: (marker, children, linkProps) => (
      <Link
        to="/apps/$domain/deployments/$id"
        params={{ domain, id: String(mark.id) }}
        aria-label={marker.label}
        // The marker's state colour as a utility, rather than the kit's inline style.
        className={cx(linkProps.className, stateTextClass(marker.state))}
      >
        {children}
      </Link>
    ),
  }));

  if (status.isError && status.data === undefined) {
    return (
      <ErrorBlock error={status.error} title={t("appPages.metrics.statusLoadError")} onRetry={() => void status.refetch()} retrying={status.isRefetching} />
    );
  }

  const limits = app.data === undefined ? { cpu: null, memory: null } : appLimits(app.data);
  const locale = t.locale;
  const specs: { role: string; title: string; series: SeriesSpec[]; format: (value: number) => string; limit: { value: number; label: string } | null }[] = shownRoles.map((role) => {
    const metric = status.data?.series?.[role] ?? "";
    switch (role) {
      case "cpu":
        return {
          role,
          title: t("appPages.common.cpu"),
          series: [{ metric, label: t("appPages.common.cpu") }],
          format: (value: number) => formatPercent(value, locale),
          limit: limits.cpu === null ? null : { value: limits.cpu, label: t("appPages.metrics.limitLabel", { value: formatPercent(limits.cpu, locale) }) },
        };
      case "memory":
        return {
          role,
          title: t("appPages.common.memory"),
          series: [{ metric, label: t("appPages.common.memory") }],
          format: (value: number) => formatBytes(value, locale),
          limit: limits.memory === null ? null : { value: limits.memory, label: t("appPages.metrics.limitLabel", { value: formatBytes(limits.memory, locale) }) },
        };
      case "requests":
        return {
          role,
          title: t("appPages.metrics.requests"),
          series: [{ metric, label: t("appPages.metrics.requests") }],
          format: (value: number) => t("appPages.metrics.perMinute", { value: formatCount(Math.round(value), locale) }),
          limit: null,
        };
      default:
        return {
          role,
          title: t("appPages.metrics.errors5xx"),
          series: [{ metric, label: t("appPages.metrics.errors5xx"), state: "failed" as const }],
          format: (value: number) => t("appPages.metrics.perMinute", { value: formatCount(Math.round(value), locale) }),
          limit: null,
        };
    }
  });

  const charts = (
    <ChartGroup>
      <div className="grid min-w-0 gap-4 xl:grid-cols-2">
        {specs.map((spec) => (
          <MetricChart
            key={spec.role}
            title={spec.title}
            range={range}
            series={spec.series}
            read={read}
            prepared={prepared}
            format={spec.format}
            limit={spec.limit}
            markers={markers}
            height={CHART_HEIGHT}
            rangeControl={control}
            couldNotLoad={t("appPages.metrics.chartLoadError", { title: spec.title })}
            investigateApp={domain}
          />
        ))}
      </div>
    </ChartGroup>
  );

  if (status.data === undefined) {
    return (
      <Section title={t("appPages.metrics.mainTitle")} description={<TruthLine read={undefined} range={range} reading />} actions={control}>
        <div className="grid min-w-0 gap-4 xl:grid-cols-2" aria-busy="true">
          {[t("appPages.common.cpu"), t("appPages.common.memory")].map((name) => (
            <Card key={name} padding="sm" as="div">
              <ChartSkeleton title={name} height={CHART_HEIGHT} />
            </Card>
          ))}
        </div>
      </Section>
    );
  }

  if (shownRoles.length === 0) {
    // Nothing of this application can be measured: say why, instead of drawing empty charts.
    const words = reason ? reasonWords(t, reason) : { title: t("appPages.metrics.reason.other.title"), description: "" };
    return (
      <div data-metrics-reason={reason?.code ?? ""}>
        <EmptyState
          variant="firstUse"
          level={2}
          icon={<ChartLine />}
          title={words.title}
          description={words.description}
          {...(reason ? { action: <ReasonDetail t={t} reason={reason} /> } : {})}
        />
      </div>
    );
  }

  // No reading in the whole window: why, instead of empty charts. The plan's reason first (the
  // service is stopped, accounting is off...), then the collector's (nothing records at all).
  const collectorReason = collector !== undefined && !collector.recording ? (collector.reason ?? null) : null;
  const emptyBecause = prepared !== undefined && !hasReadings ? (reason ?? collectorReason) : null;

  return (
    <Section
      title={traffic ? t("appPages.metrics.trafficTitle") : t("appPages.metrics.mainTitle")}
      description={<TruthLine read={read.data} range={range} reading={read.isPlaceholderData} />}
      actions={control}
    >
      {emptyBecause !== null ? (
        <div data-metrics-reason={emptyBecause.code}>
          <EmptyState
            variant="firstUse"
            level={3}
            icon={<ChartLine />}
            title={emptyBecause === collectorReason ? t("overview.history.notRecordingTitle") : reasonWords(t, emptyBecause).title}
            description={emptyBecause === collectorReason ? collectorReasonText(t, emptyBecause) : reasonWords(t, emptyBecause).description}
            action={<ReasonDetail t={t} reason={emptyBecause} />}
          />
        </div>
      ) : (
        <>
          {reason !== null && hasReadings ? (
            <div data-metrics-reason={reason.code}>
              <Notice tone={reason.code === "starting" ? "info" : "warning"} title={reasonWords(t, reason).title}>
                <div className="flex flex-col gap-2">
                  <p>{`${reasonWords(t, reason).description} ${t("appPages.metrics.earlierStay")}`}</p>
                  <ReasonDetail t={t} reason={reason} />
                </div>
              </Notice>
            </div>
          ) : (
            <CollectorNotice collector={collector} />
          )}
          {charts}
          {!traffic ? <p className="text-12 text-pretty text-fg-faint">{t("appPages.metrics.cpuNote")}</p> : null}
          <DeployList domain={domain} marks={marks} t={t} />
        </>
      )}
    </Section>
  );
}
