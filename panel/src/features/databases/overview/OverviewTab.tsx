import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";

import { databaseBackupsQuery, databaseMetricsQuery, databaseOverviewQuery, databasesQuery } from "../../../api/queries/databases";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { StatTile } from "../../../components/page/StatTile";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Can } from "../../../components/page/permissions";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ChartSkeleton } from "../../../components/ui/Chart";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { formatBytes, formatCount } from "../../../lib/format";
import { MetricChart } from "../../overview/MetricChart";
import { prepareRead, useMetricsRead } from "../../overview/metricsData";
import { BackupNowButton } from "../BackupNowButton";
import { answers } from "../engineOf";
import { DatabasePage, useDatabaseFrame } from "../DatabasePage";
import { sizeBytes, sizeWords } from "../DatabasesTable";
import { can } from "../engines";
import { formatHitRatio } from "../format";
import { newestDumps } from "../protection";
import { canMoveUp, serverWithPlace, supportLine, supportOf } from "../servers";
import { VerifiedMark } from "../VerifiedMark";

/**
 * A database at a glance: its size, connections, cache and newest backup, which engine it runs on (and, when that
 * version is ending, the way to a newer one), and its size and connections over time. Nothing else is here: who
 * can reach it, how it is backed up and what it holds are the sections of the column, one press away. It says what
 * it knows while the engine is stopped (the engine itself is said above, with the way to start it).
 */
export function OverviewTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const frame = useDatabaseFrame();
  const { server, life, actions } = frame;
  const running = life !== "stopped" && life !== "changing";
  const asking = answers(frame);
  const overview = useQuery({ ...databaseOverviewQuery(engine, name), retry: false, enabled: asking });
  const row = useQuery({ ...databasesQuery(), select: (list) => list.databases.find((item) => item.engine === engine && item.name === name) });
  const database = overview.data?.database ?? row.data;
  const capabilities = overview.data?.capabilities ?? [];
  const metrics = useQuery({ ...databaseMetricsQuery(engine, name), retry: false, enabled: asking && can({ capabilities }, "metrics") });
  const dumps = useQuery(databaseBackupsQuery(engine, name));
  const newest = newestDumps(dumps.data?.backups).values().next().value;

  const series = useMemo(() => metrics.data?.series ?? {}, [metrics.data]);
  const sizeSeries = series["size"];
  const connectionSeries = series["connections"];
  const sizeRead = useMetricsRead(sizeSeries === undefined ? [] : [sizeSeries], "30d");
  const connectionRead = useMetricsRead(connectionSeries === undefined ? [] : [connectionSeries], "24h");
  const sizePrepared = useMemo(() => (sizeRead.data === undefined ? undefined : prepareRead(sizeRead.data, t)), [sizeRead.data, t]);
  const connectionPrepared = useMemo(() => (connectionRead.data === undefined ? undefined : prepareRead(connectionRead.data, t)), [connectionRead.data, t]);

  const missing = database?.missing === true;
  const dumpable = can({ capabilities }, "dump") && running && !missing;
  const size = metrics.data?.size_bytes ?? sizeBytes(database?.size);
  const state = life === "stopped" ? "stopped" : life === "changing" ? "changing" : missing ? "missing" : overview.isError ? "unreadable" : "running";
  const pill =
    state === "running" ? (
      <StatusPill state="running" label={t("common.statusPill.running")} />
    ) : state === "stopped" ? (
      <StatusPill state="stopped" label={t("common.statusPill.stopped")} />
    ) : state === "changing" ? (
      <StatusPill state="deploying" label={t("databases.state.changing")} />
    ) : state === "missing" ? (
      <StatusPill state="failed" label={t("databases.state.missing")} />
    ) : overview.isError ? (
      <StatusPill state="unknown" label={t("databases.state.unreadable")} />
    ) : (
      <Skeleton className="h-6 w-24 rounded-pill" />
    );

  const support = server === undefined ? undefined : supportOf(server, new Date());
  const supportWords = support === undefined ? null : supportLine(t, support);
  const upgradeable = server !== undefined && support !== undefined && (support.kind === "ended" || support.kind === "soon") && canMoveUp(server);
  const warnings = overview.data?.warnings ?? [];
  // What is not known yet is not drawn as if it were: the tiles' room is held, in a region that says it is busy.
  const loading = frame.reading || (asking && overview.isPending);

  return (
    <DatabasePage
      title={name}
      mono
      status={pill}
      {...(dumpable ? { primaryAction: <BackupNowButton engine={engine} name={name} /> } : {})}
    >
      {loading ? (
        <LoadingRegion label={t("databases.layout.loading", { name })} className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {(["size", "connections", "cacheHit", "lastBackup"] as const).map((tile) => (
            <StatTile key={tile} loading label={t(`databases.overview.${tile}`)} value={<Skeleton className="h-5 w-16" />} detail={<Skeleton className="h-3 w-24" />} />
          ))}
        </LoadingRegion>
      ) : (
      <div className="flex flex-col gap-6">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {/* A stopped engine says so once, in the banner: its figures are not drawn as zeroes and dashes with the same sentence under each. */}
          {running || size !== null ? (
            <StatTile
              label={t("databases.overview.size")}
              value={size !== null ? formatBytes(size, t.locale) : (sizeWords(database?.size, t) ?? "–")}
              detail={
                !running
                  ? undefined
                  : database?.keys != null
                    ? t("databases.overview.keyCount", { count: database.keys })
                    : database?.tables != null && database.tables > 0
                      ? t("databases.overview.tables", { count: database.tables })
                      : t("databases.overview.onDisk")
              }
            />
          ) : null}
          {running && can({ capabilities }, "metrics") ? (
            <>
              <StatTile
                label={t("databases.overview.connections")}
                value={metrics.data?.connections != null ? formatCount(metrics.data.connections, t.locale) : "–"}
                detail={
                  metrics.isError
                    ? t("databases.overview.metricsUnavailable")
                    : metrics.data?.max_connections != null
                      ? t("databases.overview.ofMax", { max: formatCount(metrics.data.max_connections, t.locale) })
                      : t("databases.overview.now")
                }
              />
              <StatTile
                label={t("databases.overview.cacheHit")}
                value={metrics.data?.cache_hit_ratio != null ? formatHitRatio(metrics.data.cache_hit_ratio, t.locale) : "–"}
                detail={metrics.isError ? t("databases.overview.metricsUnavailable") : t("databases.overview.cacheHitDetail")}
              />
            </>
          ) : null}
          <StatTile
            label={t("databases.overview.lastBackup")}
            value={newest !== undefined ? <RelativeTime value={newest.created} className="title text-18 text-fg" /> : t("databases.overview.never")}
            detail={newest !== undefined ? <VerifiedMark status={newest.verify_status} compact /> : t("databases.overview.noDumps")}
          />
        </div>

        {warnings.length > 0 ? (
          <div className="flex flex-col gap-2">
            {warnings.map((warning) => (
              <Notice key={warning} tone="warning" title={t("databases.overview.engineWarning", { engine: overview.data?.display_name ?? "" })}>
                <span translate="no">{warning}</span>
              </Notice>
            ))}
          </div>
        ) : null}

        {server !== undefined ? (
          <SurfaceSection
            title={t("databases.overview.engine")}
            icon={OBJECT_ICONS.server}
            description={serverWithPlace(t, server)}
            status={
              life === "stopped" ? undefined : support !== undefined && (support.kind === "ended" || support.kind === "soon") && supportWords !== null ? (
                <StatusPill state={support.kind === "ended" ? "failed" : "warning"} size="sm" label={supportWords} />
              ) : supportWords !== null ? (
                <StatusPill state="running" size="sm" label={supportWords} />
              ) : undefined
            }
            actions={
              upgradeable ? (
                <Can permission="databases.manage">
                  <Button size="sm" disabled={actions.busy} onClick={() => actions.upgrade(server)}>
                    {t("dbServers.upgradeVersion")}
                  </Button>
                </Can>
              ) : (
                <Link to="/databases/engines" className={buttonClassName("ghost", "sm")}>
                  {t("databases.overview.seeEngines")}
                </Link>
              )
            }
          />
        ) : null}

        {running && (sizeSeries !== undefined || connectionSeries !== undefined) ? (
          <div className="grid gap-4 lg:grid-cols-2">
            {sizeSeries !== undefined ? (
              <MetricChart
                title={t("databases.metrics.size")}
                range="30d"
                series={[{ metric: sizeSeries, label: t("databases.metrics.size") }]}
                read={sizeRead}
                prepared={sizePrepared}
                format={(value) => formatBytes(value, t.locale)}
                fit
                height={120}
                rangeControl={null}
                couldNotLoad={t("databases.metrics.couldNotLoad")}
                empty={t("databases.metrics.noHistory")}
              />
            ) : null}
            {connectionSeries !== undefined ? (
              <MetricChart
                title={t("databases.metrics.connections")}
                range="24h"
                series={[{ metric: connectionSeries, label: t("databases.metrics.connections") }]}
                read={connectionRead}
                prepared={connectionPrepared}
                format={(value) => formatCount(Math.round(value), t.locale)}
                height={120}
                rangeControl={null}
                couldNotLoad={t("databases.metrics.couldNotLoad")}
                empty={t("databases.metrics.noHistory")}
              />
            ) : null}
          </div>
        ) : running && metrics.isPending && can({ capabilities }, "metrics") ? (
          // The charts' place is held while the engine says which it has, or what is under them would be pushed down when they arrive.
          <div className="grid gap-4 lg:grid-cols-2">
            <Card padding="sm" as="div">
              <ChartSkeleton title={t("databases.metrics.size")} height={120} />
            </Card>
            <Card padding="sm" as="div">
              <ChartSkeleton title={t("databases.metrics.connections")} height={120} />
            </Card>
          </div>
        ) : null}
      </div>
      )}
    </DatabasePage>
  );
}
