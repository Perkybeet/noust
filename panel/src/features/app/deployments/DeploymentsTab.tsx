import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Rocket } from "lucide-react";

import { appQuery } from "../../../api/queries/apps";
import type { Deployment } from "../../../api/queries/deployments";
import { deploymentPagesQuery } from "../../../api/queries/deployments";
import { useDocumentTitle } from "../../../app/documentTitle";
import { DeployStatePill } from "../../../components/page/AppStatePill";
import { useNow } from "../../../components/page/clock";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Badge } from "../../../components/ui/Badge";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount, formatDuration, parseTimestamp } from "../../../lib/format";
import { ReleasesSection } from "./ReleasesSection";
import { shortCommit, triggerWords } from "./words";

/** Rows per page. The store keeps the last twenty deploys of an app. */
export const DEPLOYMENTS_PAGE = 10;

const RUNNING = new Set(["queued", "running"]);

/** How long a deploy took, or has been running for. */
function Duration({ deployment, t }: { deployment: Deployment; t: T }) {
  const running = RUNNING.has(deployment.status);
  const started = parseTimestamp(deployment.started_at);
  const now = useNow(() => (running ? 1_000 : 3_600_000));
  if (running) {
    if (started === null) return <span className="text-fg-faint">-</span>;
    return <span className="text-fg-muted">{formatDuration(Math.max(0, (now - started.getTime()) / 1000), t.locale)}</span>;
  }
  if (deployment.duration_s === null || deployment.duration_s === undefined) return <span className="text-fg-faint">-</span>;
  return <>{formatDuration(deployment.duration_s, t.locale)}</>;
}

function columns(domain: string, t: T): Column<Deployment>[] {
  return [
    {
      id: "id",
      header: t("appPages.deployments.tab.deployColumn"),
      width: "w-20",
      cell: (row) => (
        <Link
          to="/apps/$domain/deployments/$id"
          params={{ domain, id: String(row.id) }}
          className="mono -mx-1 inline-flex h-7 items-center rounded-[4px] px-1 text-12 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
        >
          <span className="sr-only">{t("appPages.deployments.page.heading", { id: String(row.id) })}</span>
          <span aria-hidden="true">{`#${String(row.id)}`}</span>
        </Link>
      ),
    },
    {
      id: "status",
      header: t("appPages.deployments.tab.statusColumn"),
      width: "w-36",
      cell: (row) => <DeployStatePill status={row.status} appearance="inline" size="sm" />,
    },
    {
      id: "commit",
      header: t("appPages.deployments.tab.commitColumn"),
      cell: (row) => {
        const commit = shortCommit(row.git_commit);
        return (
          <div className="flex min-w-0 flex-col py-1.5 leading-4">
            <span className="flex min-w-0 items-baseline gap-2">
              {commit ? (
                <span translate="no" className="mono text-12 text-fg">
                  {commit}
                </span>
              ) : (
                <span className="text-12 text-fg-faint">{t("appPages.deployments.tab.noCommit")}</span>
              )}
              {row.git_branch ? (
                <span translate="no" className="mono hidden truncate text-12 text-fg-faint sm:inline">
                  {row.git_branch}
                </span>
              ) : null}
            </span>
            {row.commit_message ? (
              <span title={row.commit_message} className="hidden max-w-[24rem] truncate text-12 text-fg-faint md:block">
                {row.commit_message}
              </span>
            ) : null}
          </div>
        );
      },
    },
    {
      id: "trigger",
      header: t("appPages.deployments.fields.startedBy"),
      hideBelow: "md",
      cell: (row) => {
        const words = triggerWords(t, row.triggered_by);
        const Icon = words.icon;
        return (
          <span className="flex items-center gap-1.5 text-fg-muted">
            <Icon aria-hidden="true" className="size-3.5 shrink-0" />
            {words.label}
          </span>
        );
      },
    },
    {
      id: "started",
      header: t("appPages.deployments.fields.started"),
      cell: (row) => <RelativeTime value={row.started_at} className="text-fg-muted" />,
    },
    {
      id: "duration",
      header: t("appPages.deployments.fields.duration"),
      align: "end",
      mono: true,
      hideBelow: "sm",
      cell: (row) => <Duration deployment={row} t={t} />,
    },
  ];
}

function History({ domain, t }: { domain: string; t: T }) {
  const pages = useInfiniteQuery(deploymentPagesQuery({ domain, limit: DEPLOYMENTS_PAGE }));
  const rows = pages.data?.pages.flatMap((page) => page.items) ?? [];
  const total = pages.data?.pages[0]?.total ?? 0;

  if (pages.isError && pages.data === undefined) {
    return (
      <Section title={t("appPages.deployments.tab.historyTitle")}>
        <ErrorBlock error={pages.error} title={t("appPages.deployments.tab.loadError")} onRetry={() => void pages.refetch()} retrying={pages.isRefetching} />
      </Section>
    );
  }

  return (
    <Section
      title={t("appPages.deployments.tab.historyTitle")}
      description={t("appPages.deployments.tab.historyDescription")}
      badge={
        pages.data ? (
          // The same count badge every section title carries.
          <Badge>
            {formatCount(total, t.locale)}
            <span className="sr-only"> {t("appPages.deployments.tab.inTotal")}</span>
          </Badge>
        ) : undefined
      }
    >
      <DataTable
        caption={t("appPages.deployments.tab.caption", { domain })}
        columns={columns(domain, t)}
        rows={rows}
        getRowId={(row) => String(row.id)}
        loading={pages.isPending}
        density="compact"
        empty={
          <EmptyState
            level={3}
            icon={<Rocket />}
            title={t("appPages.deployments.tab.emptyTitle")}
            description={t("appPages.deployments.tab.emptyDescription")}
            command={`wasm update ${domain}`}
            className="py-8"
          />
        }
      />
      {pages.isError ? (
        <ErrorBlock compact live error={pages.error} title={t("appPages.deployments.tab.olderLoadError")} onRetry={() => void pages.fetchNextPage()} />
      ) : null}
      {pages.hasNextPage ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" loading={pages.isFetchingNextPage} onClick={() => void pages.fetchNextPage()}>
            {t("appPages.deployments.tab.loadOlder")}
          </Button>
          <span className="text-12 text-fg-faint">{t("appPages.deployments.tab.showing", { shown: formatCount(rows.length, t.locale), total: formatCount(total, t.locale) })}</span>
        </div>
      ) : rows.length > DEPLOYMENTS_PAGE ? (
        <p className="text-12 text-fg-faint">{t("appPages.deployments.tab.allShown", { total: formatCount(total, t.locale) })}</p>
      ) : null}
    </Section>
  );
}

/**
 * Every deploy of one app, and what it can go back to: its releases, switched in seconds, or
 * for an app deployed in place, the backups a rollback restores.
 */
export function DeploymentsTab({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.deployments.tab.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));

  return (
    <div className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <History domain={domain} t={t} />
      {app.data ? <ReleasesSection domain={domain} layout={app.data.layout} /> : null}
    </div>
  );
}
