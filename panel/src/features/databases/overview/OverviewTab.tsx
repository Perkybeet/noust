import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { databaseBackupsQuery, databaseMetricsQuery, databaseOverviewQuery, policyQuery } from "../../../api/queries/databases";
import type { DatabaseOverview } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { StatTile } from "../../../components/page/StatTile";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatCount } from "../../../lib/format";
import { formatHitRatio } from "../format";
import { MetricChart } from "../../overview/MetricChart";
import { prepareRead, useMetricsRead } from "../../overview/metricsData";
import { policyScheduleWords, retentionWords } from "../backups/schedule";
import { sizeBytes, sizeWords } from "../DatabasesTable";
import { can, supportText, supportView } from "../engines";
import { useDatabaseJob } from "../jobs";
import { LinkDialog } from "../LinkDialog";
import { newestDumps, policyProtection, protectionView } from "../protection";
import { TEXT_LINK } from "../ui";
import { profileLabel } from "../users/profiles";
import { VerifiedMark } from "../VerifiedMark";

/** How many accounts the overview names before sending to the Users tab. */
const ACCESS_SHOWN = 5;

function TilesSkeleton() {
  return (
    <div aria-hidden="true" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {[0, 1, 2, 3].map((index) => (
        <StatTile key={index} label=" " value={<Skeleton className="h-5 w-16" />} detail={<Skeleton className="h-3 w-24" />} />
      ))}
    </div>
  );
}

/** The database's facts, one per row: what the engine and Noust know of it. */
function aboutItems(overview: DatabaseOverview, t: T): KeyValueItem[] {
  const database = overview.database;
  const support = supportText(t, overview.support);
  const items: KeyValueItem[] = [
    { label: t("databases.fields.engine"), value: `${overview.display_name}${database.engine_version ? ` ${database.engine_version}` : ""}`, copy: false },
  ];
  if (support !== null) items.push({ label: t("databases.overview.support"), value: support, mono: false, copy: false });
  if (database.owner) items.push({ label: t("databases.fields.owner"), value: database.owner });
  if (database.encoding) items.push({ label: t("databases.overview.encoding"), value: database.encoding });
  items.push(
    { label: t("databases.overview.port"), value: String(overview.port) },
    { label: t("databases.overview.service"), value: overview.service },
    {
      label: t("databases.overview.tracked"),
      value: database.tracked ? t("databases.overview.trackedYes") : t("databases.overview.trackedNo"),
      mono: false,
      copy: false,
    },
  );
  return items;
}

/**
 * A database at a glance: its size, connections, cache and newest backup; what uses it, who
 * can reach it, how it is backed up, and the facts about it. Everything links to the tab that
 * does more with it.
 */
export function OverviewTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const job = useDatabaseJob();
  const [linking, setLinking] = useState(false);
  const overview = useQuery(databaseOverviewQuery(engine, name));
  const metrics = useQuery({ ...databaseMetricsQuery(engine, name), retry: false });
  const policy = useQuery(policyQuery(engine, name));
  const dumps = useQuery(databaseBackupsQuery(engine, name));
  const data = overview.data;
  const capabilities = data?.capabilities ?? [];
  const params = { engine, name };

  const series = useMemo(() => metrics.data?.series ?? {}, [metrics.data]);
  const chartMetrics = useMemo(() => [series["size"], series["connections"]].filter((metric): metric is string => metric !== undefined), [series]);
  const read = useMetricsRead(chartMetrics, "24h", chartMetrics.length > 0);
  const prepared = useMemo(() => (read.data === undefined ? undefined : prepareRead(read.data, t)), [read.data, t]);

  if (data === undefined) {
    if (overview.isError) return null; // The layout's banner says it, with Try again.
    return (
      <div aria-busy="true" className="flex flex-col gap-8">
        <span className="sr-only">{t("databases.overview.loading")}</span>
        <TilesSkeleton />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-48 rounded-card" />
          <Skeleton className="h-48 rounded-card" />
        </div>
      </div>
    );
  }

  const database = data.database;
  const newest = newestDumps(dumps.data?.backups).values().next().value;
  const support = supportView(data.support);
  const supportWords = supportText(t, data.support);
  const protection = protectionView(policyProtection(policy.data));
  const accounts = data.access.filter((entry) => !entry.internal);
  const size = metrics.data?.size_bytes ?? sizeBytes(database.size);

  return (
    <div className="flex flex-col gap-8">
      {data.warnings.length > 0 || support?.warn ? (
        <div className="flex flex-col gap-2">
          {support?.warn && supportWords !== null ? (
            <Notice tone="warning" title={supportWords}>
              {t("databases.overview.supportAdvice")}
            </Notice>
          ) : null}
          {data.warnings.map((warning) => (
            <Notice key={warning} tone="warning" title={t("databases.overview.engineWarning", { engine: data.display_name })}>
              <span translate="no">{warning}</span>
            </Notice>
          ))}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile
          label={t("databases.overview.size")}
          value={size !== null ? formatBytes(size, t.locale) : (sizeWords(database.size, t) ?? "–")}
          detail={
            database.keys != null
              ? t("databases.overview.keyCount", { count: database.keys })
              : database.tables != null && database.tables > 0
                ? t("databases.overview.tables", { count: database.tables })
                : t("databases.overview.onDisk")
          }
        />
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
        <StatTile
          label={t("databases.overview.lastBackup")}
          value={newest !== undefined ? <RelativeTime value={newest.created} className="title text-18 text-fg" /> : t("databases.overview.never")}
          detail={newest !== undefined ? <VerifiedMark status={newest.verify_status} compact /> : t("databases.overview.noDumps")}
        />
      </div>

      {chartMetrics.length > 0 ? (
        <Section title={t("databases.overview.lastDay")}>
        <div className="grid gap-4 lg:grid-cols-2">
          {series["size"] !== undefined ? (
            <MetricChart
              title={t("databases.metrics.size")}
              range="24h"
              series={[{ metric: series["size"], label: t("databases.metrics.size") }]}
              read={read}
              prepared={prepared}
              format={(value) => formatBytes(value, t.locale)}
              fit
              height={120}
              rangeControl={null}
              couldNotLoad={t("databases.metrics.couldNotLoad")}
              empty={t("databases.metrics.noHistory")}
            />
          ) : null}
          {series["connections"] !== undefined ? (
            <MetricChart
              title={t("databases.metrics.connections")}
              range="24h"
              series={[{ metric: series["connections"], label: t("databases.metrics.connections") }]}
              read={read}
              prepared={prepared}
              format={(value) => formatCount(Math.round(value), t.locale)}
              height={120}
              rangeControl={null}
              couldNotLoad={t("databases.metrics.couldNotLoad")}
              empty={t("databases.metrics.noHistory")}
            />
          ) : null}
        </div>
        </Section>
      ) : null}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-4">
          <Card
            title={t("databases.overview.usedBy")}
            level={2}
            description={t("databases.overview.usedByDescription")}
            padding="sm"
            actions={
              <Button size="sm" disabled={database.missing} onClick={() => setLinking(true)}>
                {t("databases.overview.linkApp")}
              </Button>
            }
          >
            {data.links.length === 0 ? (
              <EmptyState variant="inline" title={t("databases.overview.noApps")} />
            ) : (
              <ul className="flex flex-col divide-y divide-border">
                {data.links.map((link) => (
                  <li key={link.domain} className="flex min-w-0 flex-col gap-0.5 py-2 first:pt-0 last:pb-0">
                    <Link to="/apps/$domain/database" params={{ domain: link.domain }} translate="no" className={`${TEXT_LINK} w-fit text-13`}>
                      {link.domain}
                    </Link>
                    <span className="flex min-w-0 flex-wrap items-center gap-x-2 text-12 text-fg-muted">
                      {link.env_var ? <Mono tone="default">{link.env_var}</Mono> : null}
                      {link.url ? (
                        <Mono tone="muted" truncate>
                          {link.url}
                        </Mono>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card
            title={t("databases.overview.access")}
            level={2}
            padding="sm"
            actions={
              can({ capabilities }, "users") ? (
                <Link to="/databases/$engine/$name/users" params={params} className={buttonClassName("secondary", "sm")}>
                  {t("databases.overview.manageUsers")}
                </Link>
              ) : undefined
            }
          >
            {accounts.length === 0 ? (
              <EmptyState variant="inline" title={t("databases.overview.noAccounts")} />
            ) : (
              <ul className="flex flex-col divide-y divide-border">
                {accounts.slice(0, ACCESS_SHOWN).map((entry) => (
                  <li key={`${entry.username}@${entry.host}`} className="flex min-w-0 items-center justify-between gap-3 py-2 text-13 first:pt-0 last:pb-0">
                    <Mono truncate>{entry.username}</Mono>
                    <span className="shrink-0 text-fg-muted">{t(profileLabel(entry.profile))}</span>
                  </li>
                ))}
                {accounts.length > ACCESS_SHOWN ? (
                  <li className="pt-2 text-12 text-fg-muted">{t("databases.overview.moreAccounts", { count: accounts.length - ACCESS_SHOWN })}</li>
                ) : null}
              </ul>
            )}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {can({ capabilities }, "dump") ? (
            <Card
              title={t("databases.overview.backups")}
              level={2}
              padding="sm"
              actions={
                <Link to="/databases/$engine/$name/backups" params={params} className={buttonClassName("secondary", "sm")}>
                  {policy.data?.configured ? t("databases.overview.openBackups") : t("databases.overview.setSchedule")}
                </Link>
              }
            >
              {policy.isError ? (
                <ErrorBlock compact error={policy.error} title={t("databases.overview.policyFailed")} onRetry={() => void policy.refetch()} />
              ) : policy.data === undefined ? (
                <KeyValueListSkeleton rows={3} />
              ) : (
                <div className="flex flex-col gap-3">
                  <StatusPill appearance="inline" size="sm" state={protection.state} label={t(protection.label)} />
                  {policy.data.configured ? (
                    <KeyValueList
                      items={[
                        { label: t("databases.overview.schedule"), value: policyScheduleWords(policy.data, t.locale), mono: false, copy: false },
                        {
                          label: t("databases.overview.keeps"),
                          value: retentionWords(t, policy.data.retention_count, policy.data.retention_days),
                          mono: false,
                          copy: false,
                        },
                        {
                          label: t("databases.overview.sentTo"),
                          value:
                            (policy.data.destinations ?? []).length > 0
                              ? (policy.data.destinations ?? []).map((destination) => destination.name).join(", ")
                              : t("databases.overview.onlyHere"),
                          mono: (policy.data.destinations ?? []).length > 0,
                          copy: false,
                        },
                        {
                          label: t("databases.overview.lastRun"),
                          value: policy.data.last_run_at ? <RelativeTime value={policy.data.last_run_at} /> : t("databases.overview.notYet"),
                          mono: false,
                          copy: false,
                        },
                      ]}
                    />
                  ) : (
                    <p className="text-13 text-fg-muted">{t("databases.overview.noPolicy")}</p>
                  )}
                </div>
              )}
            </Card>
          ) : null}

          <Card title={t("databases.overview.about")} level={2} padding="sm">
            <KeyValueList items={aboutItems(data, t)} />
          </Card>
        </div>
      </div>

      <CommandHint command={`noust db info ${name} -e ${engine}`} label={t("databases.common.fromTerminal")} />

      <LinkDialog
        open={linking}
        onOpenChange={setLinking}
        engine={engine}
        database={name}
        onQueued={(accepted, domain) => job.track(accepted, "link", domain)}
      />
    </div>
  );
}
