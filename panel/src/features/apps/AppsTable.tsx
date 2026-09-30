import { Link } from "@tanstack/react-router";
import { GitPullRequest } from "lucide-react";
import type { ReactNode } from "react";

import type { MetricsSnapshot } from "../../api/queries/metrics";
import { AppStatePill } from "../../components/page/AppStatePill";
import { RelativeTime } from "../../components/page/RelativeTime";
import { STATE_RANK, appStatus, deployStatus } from "../../components/page/status";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes, formatPercent, parseTimestamp } from "../../lib/format";
import type { AppInfo, Deployment, LastDeployment } from "./data";
import { appReading, deployMoment, lastDeployOf, previewParentOf, readsApps, useTypeName } from "./data";

/** A deployment's outcome as a glyph and when it happened, for dense rows. */
export function DeployMoment({ deploy }: { deploy: Pick<Deployment, "status" | "finished_at"> & { started_at?: string | null } }) {
  const t = useT();
  const view = deployStatus(deploy.status, t.locale);
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusGlyph state={view.state} size={10} className={stateTextClass(view.state)} />
      <span className="sr-only">{`${view.label}, `}</span>
      <RelativeTime value={deployMoment(deploy)} className="text-fg-muted" />
    </span>
  );
}

/**
 * The app's domain, a link to its page; a pull request preview says whose it is underneath, so
 * a list with previews in it still reads as the apps the operator deployed.
 */
export function AppName({ t, app }: { t: T; app: AppInfo }) {
  const parent = previewParentOf(app);
  const link = (
    <Link
      to="/apps/$domain"
      params={{ domain: app.domain }}
      title={app.domain}
      className="-mx-1 block max-w-full truncate rounded-chip px-1 py-0.5 font-medium text-fg hover:underline hover:underline-offset-2"
    >
      {app.domain}
    </Link>
  );
  if (parent === null) return link;
  return (
    <span className="flex max-w-full min-w-0 flex-col items-start">
      {link}
      <span className="inline-flex max-w-full min-w-0 items-center gap-1 text-12 text-fg-muted">
        <GitPullRequest aria-hidden="true" className="size-icon-xs shrink-0 text-fg-faint" />
        <span className="truncate">{t.rich("apps.table.previewOf", { parent: <Mono tone="muted">{parent}</Mono> })}</span>
      </span>
    </span>
  );
}

interface Row {
  app: AppInfo;
  deploy: LastDeployment | Deployment | null;
  cpu: number | null;
  memory: number | null;
}

export interface AppsTableProps {
  apps: readonly AppInfo[];
  /** The newest deploy of each domain (latestDeployByDomain), when the page read the history. */
  deploys: ReadonlyMap<string, Deployment>;
  metrics: MetricsSnapshot | undefined;
  caption: string;
  loading?: boolean;
  /** How many rows to draw while loading: the count the page expects, so nothing jumps. */
  skeletonRows?: number;
  /** Shown in place of rows when there are none. */
  empty?: ReactNode;
  /** A per-row menu; the list offers one. On a phone it stays in view on every card. */
  rowActions?: (app: AppInfo) => ReactNode;
  className?: string;
}

/**
 * Every application, identity first: its domain (a link to it, so it opens in a new tab like
 * any other link), its state, its type by the name people know it by, its last deploy and, when
 * the collector reads them, its CPU and memory now. On a phone each row is a card with its
 * actions always in view.
 */
export function AppsTable({ apps, deploys, metrics, caption, loading = false, skeletonRows, empty, rowActions, className }: AppsTableProps) {
  const t = useT();
  const typeName = useTypeName();
  const rows: Row[] = apps.map((app) => ({ app, deploy: lastDeployOf(app, deploys), ...appReading(metrics, app.domain) }));
  const withReadings = readsApps(metrics);

  const columns: Column<Row>[] = [
    {
      id: "domain",
      header: t("apps.table.columnApplication"),
      cell: (row) => <AppName t={t} app={row.app} />,
      sortValue: (row) => row.app.domain,
    },
    {
      id: "state",
      header: t("apps.table.columnState"),
      width: "w-32",
      card: "status",
      cell: (row) => <AppStatePill status={row.app.status} appearance="inline" size="sm" />,
      sortValue: (row) => STATE_RANK[appStatus(row.app.status).state],
    },
    {
      id: "type",
      header: t("apps.table.columnType"),
      width: "w-36",
      hideBelow: "sm",
      cell: (row) => {
        const name = typeName(row.app.app_type);
        return name === null ? <EmptyCell reason={t("apps.table.unknownType")} /> : <span className="text-fg-muted">{name}</span>;
      },
      sortValue: (row) => typeName(row.app.app_type),
    },
    {
      id: "deploy",
      header: t("apps.table.columnLastDeploy"),
      width: "w-40",
      cell: (row) => (row.deploy ? <DeployMoment deploy={row.deploy} /> : <EmptyCell reason={t("apps.table.noRecentDeploy")} />),
      sortValue: (row) => {
        const moment = row.deploy ? parseTimestamp(deployMoment(row.deploy)) : null;
        return moment === null ? null : -moment.getTime();
      },
    },
    ...(withReadings
      ? ([
          {
            id: "cpu",
            header: t("apps.table.columnCpu"),
            align: "end",
            mono: true,
            width: "w-24",
            hideBelow: "md",
            card: "hidden",
            cell: (row) => (row.cpu === null ? <EmptyCell reason={t("apps.table.noReading")} /> : formatPercent(row.cpu, t.locale)),
            sortValue: (row) => row.cpu,
          },
          {
            id: "memory",
            header: t("apps.table.columnMemory"),
            align: "end",
            mono: true,
            width: "w-28",
            hideBelow: "md",
            card: "hidden",
            cell: (row) => (row.memory === null ? <EmptyCell reason={t("apps.table.noReading")} /> : formatBytes(row.memory, t.locale)),
            sortValue: (row) => row.memory,
          },
        ] satisfies Column<Row>[])
      : []),
  ];

  return (
    <DataTable
      columns={columns}
      rows={rows}
      getRowId={(row) => row.app.domain}
      caption={caption}
      loading={loading}
      {...(skeletonRows !== undefined ? { skeletonRows } : {})}
      {...(empty !== undefined ? { empty } : {})}
      {...(rowActions ? { rowActions: (row: Row) => rowActions(row.app) } : {})}
      defaultSort={{ column: "domain", direction: "ascending" }}
      mobile="cards"
      {...(className !== undefined ? { className } : {})}
    />
  );
}
