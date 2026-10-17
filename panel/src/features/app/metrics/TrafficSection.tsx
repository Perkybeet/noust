/**
 * What the web server's access log says about an application: requests and how they ended, by
 * the hour (by the day beyond two days), the bytes served, and the pages, networks, status codes
 * and referrers that appear most. A visitor is their network (a /24 or a /48), never their
 * address, and no query string is kept: the server never records either, so there is none to hide.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { TRAFFIC_WINDOWS, appTrafficQuery } from "../../../api/queries/appTraffic";
import type { AppTraffic, TrafficWindow } from "../../../api/queries/appTraffic";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { StatTile } from "../../../components/page/StatTile";
import { Subsection } from "../../../components/page/Subsection";
import { Card } from "../../../components/ui/Card";
import { Chart, ChartSkeleton } from "../../../components/ui/Chart";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { formatBytes, formatCount, formatPercent } from "../../../lib/format";

const CHART_HEIGHT = 180;
const DAY_S = 86_400;

/** One ranking: what was counted, most first, with how many. */
function Ranking({ title, entries }: { title: string; entries: readonly { key: string; count: number }[] }) {
  const t = useT();
  return (
    <Card padding="sm" as="div">
      <Subsection title={title} level={4}>
        {entries.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("appTraffic.traffic.rankingEmpty")}</p>
        ) : (
          <ol aria-label={title} className="flex min-w-0 flex-col gap-1">
            {entries.map((entry) => (
              <li key={entry.key} className="flex min-w-0 items-baseline justify-between gap-3">
                <Mono truncate className="min-w-0 flex-1 text-12">
                  {entry.key}
                </Mono>
                <span className="shrink-0 text-12 text-fg-muted tabular-nums">{formatCount(entry.count, t.locale)}</span>
              </li>
            ))}
          </ol>
        )}
      </Subsection>
    </Card>
  );
}

function TrafficBody({ traffic }: { traffic: AppTraffic }) {
  const t = useT();
  const { totals } = traffic;
  const daily = traffic.step >= DAY_S;
  const chart = useMemo(
    () => ({
      timestamps: traffic.points.map((point) => point.ts),
      series: [
        { label: t("appTraffic.traffic.chartTitle"), values: traffic.points.map((point) => point.requests) },
        { label: t("appPages.metrics.errors5xx"), values: traffic.points.map((point) => point.s5xx), state: "failed" as const },
      ],
      domain: [traffic.start, traffic.end] as const,
      step: traffic.step,
      resolution: t(daily ? "appTraffic.traffic.daily" : "appTraffic.traffic.hourly"),
      cell: t(daily ? "appTraffic.traffic.dayCell" : "appTraffic.traffic.hourCell"),
    }),
    [traffic, daily, t],
  );
  const ok = totals.s2xx + totals.s3xx;
  const share = totals.requests === 0 ? 0 : (totals.s5xx / totals.requests) * 100;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label={t("appTraffic.traffic.requests")} value={formatCount(totals.requests, t.locale)} detail={t("appTraffic.traffic.requestsDetail", { ok: formatCount(ok, t.locale) })} />
        <StatTile
          label={t("appTraffic.traffic.errors")}
          value={formatCount(totals.s5xx, t.locale)}
          detail={t("appTraffic.traffic.errorsDetail", { share: formatPercent(share, t.locale) })}
        />
        <StatTile label={t("appTraffic.traffic.clientErrors")} value={formatCount(totals.s4xx, t.locale)} />
        <StatTile label={t("appTraffic.traffic.served")} value={formatBytes(totals.bytes, t.locale)} />
      </div>
      {totals.requests === 0 ? (
        <p role="status" className="text-13 text-fg-muted">
          {t("appTraffic.traffic.noRequests")}
        </p>
      ) : (
        <Card padding="sm" as="div">
          <Chart
            title={t("appTraffic.traffic.chartTitle")}
            description={t(`appTraffic.traffic.window.${traffic.window as TrafficWindow}`)}
            {...chart}
            formatValue={(value) => formatCount(Math.round(value), t.locale)}
            height={CHART_HEIGHT}
          />
        </Card>
      )}
      <div className="grid min-w-0 gap-4 md:grid-cols-2">
        <Ranking title={t("appTraffic.traffic.rankings.paths")} entries={traffic.top["paths"] ?? []} />
        <Ranking title={t("appTraffic.traffic.rankings.networks")} entries={traffic.top["networks"] ?? []} />
        <Ranking title={t("appTraffic.traffic.rankings.statuses")} entries={traffic.top["statuses"] ?? []} />
        <Ranking title={t("appTraffic.traffic.rankings.referrers")} entries={traffic.top["referrers"] ?? []} />
      </div>
    </div>
  );
}

/** The traffic of one application: its window, its totals, its requests over time and what stands out. */
export function TrafficSection({ domain }: { domain: string }) {
  const t = useT();
  const [window, setWindow] = useState<TrafficWindow>("24h");
  const query = useQuery(appTrafficQuery(domain, window));
  return (
    <Subsection
      title={t("appTraffic.traffic.title")}
      description={t("appTraffic.traffic.description")}
      actions={
        <SegmentedControl<TrafficWindow>
          label={t("appTraffic.traffic.windowLabel")}
          options={TRAFFIC_WINDOWS.map((value) => ({ value, label: t(`appTraffic.traffic.window.${value}`) }))}
          value={window}
          onValueChange={setWindow}
        />
      }
    >
      {query.data !== undefined ? (
        <TrafficBody traffic={query.data} />
      ) : query.isError ? (
        <ErrorBlock compact error={query.error} title={t("appTraffic.traffic.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : (
        <div aria-busy="true" className="flex min-w-0 flex-col gap-3">
          <span className="sr-only">{t("appTraffic.traffic.loading")}</span>
          <Skeleton className="h-20 w-full" />
          <Card padding="sm" as="div">
            <ChartSkeleton title={t("appTraffic.traffic.chartTitle")} height={CHART_HEIGHT} />
          </Card>
        </div>
      )}
    </Subsection>
  );
}
