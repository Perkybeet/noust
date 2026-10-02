import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount, formatDuration, parseTimestamp } from "../../../lib/format";
import { ReleasesSection } from "./ReleasesSection";
import { deploymentStatusWord, shortCommit, triggerWords } from "./words";

/** Rows per page. The store keeps the last twenty deploys of an app. */
export const DEPLOYMENTS_PAGE = 10;

const RUNNING = new Set(["queued", "running"]);

/** How long a deploy took, or has been running for. */
function Duration({ deployment, t }: { deployment: Deployment; t: T }) {
  const running = RUNNING.has(deployment.status);
  const started = parseTimestamp(deployment.started_at);
  const now = useNow(() => (running ? 1_000 : 3_600_000));
  if (running) {
    if (started === null) return <EmptyCell reason={t("appPages.deployments.tab.notStarted")} />;
    return <span className="text-fg-muted">{formatDuration(Math.max(0, (now - started.getTime()) / 1000), t.locale)}</span>;
  }
  if (deployment.duration_s === null || deployment.duration_s === undefined) return <EmptyCell reason={t("appPages.common.notRecorded")} />;
  return <>{formatDuration(deployment.duration_s, t.locale)}</>;
}

function columns(domain: string, t: T): Column<Deployment>[] {
  return [
    {
      id: "id",
      header: t("appPages.deployments.tab.deployColumn"),
      width: "w-20",
      cell: (row) => (
        <TextLink to="/apps/$domain/deployments/$id" params={{ domain, id: String(row.id) }} className="-mx-1 inline-flex h-7 items-center px-1">
          <span className="sr-only">{t("appPages.deployments.page.heading", { id: String(row.id) })}</span>
          <span aria-hidden="true" className="tabular-nums">{`#${String(row.id)}`}</span>
        </TextLink>
      ),
    },
    {
      id: "status",
      header: t("appPages.deployments.tab.statusColumn"),
      width: "w-36",
      card: "status",
      cell: (row) => <DeployStatePill status={deploymentStatusWord(row)} appearance="inline" size="sm" />,
    },
    {
      id: "commit",
      header: t("appPages.deployments.tab.commitColumn"),
      cell: (row) => {
        const commit = shortCommit(row.git_commit);
        return (
          <span className="flex min-w-0 flex-col py-1.5">
            <span className="flex min-w-0 items-baseline gap-2">
              {commit ? <Mono>{commit}</Mono> : <span className="text-12 text-fg-faint">{t("appPages.deployments.tab.noCommit")}</span>}
              {row.git_branch ? (
                <Mono tone="faint" className="hidden truncate sm:inline">
                  {row.git_branch}
                </Mono>
              ) : null}
              {row.schema_changed ? <Badge className="shrink-0">{t("appPages.deployments.hooks.schemaChangedBadge")}</Badge> : null}
            </span>
            {row.commit_message ? (
              <span title={row.commit_message} className="hidden max-w-96 truncate text-12 text-fg-faint md:block">
                {row.commit_message}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "trigger",
      header: t("appPages.deployments.fields.startedBy"),
      hideBelow: "md",
      card: "hidden",
      cell: (row) => {
        const words = triggerWords(t, row.triggered_by);
        const Icon = words.icon;
        return (
          <span className="flex items-center gap-1.5 text-fg-muted">
            <Icon aria-hidden="true" className="size-icon-sm shrink-0" />
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

  if (pages.data !== undefined && rows.length === 0) {
    return (
      <Section title={t("appPages.deployments.tab.historyTitle")}>
        <EmptyState
          variant="firstUse"
          level={3}
          icon={<Rocket />}
          title={t("appPages.deployments.tab.emptyTitle")}
          description={t("appPages.deployments.tab.emptyDescription")}
          command={`noust update ${domain}`}
        />
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
        skeletonRows={DEPLOYMENTS_PAGE}
        density="compact"
        mobile="cards"
      />
      {pages.isError ? (
        <ErrorBlock compact live error={pages.error} title={t("appPages.deployments.tab.olderLoadError")} onRetry={() => void pages.fetchNextPage()} />
      ) : null}
      {pages.hasNextPage ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" loading={pages.isFetchingNextPage} onClick={() => void pages.fetchNextPage()}>
            {t("appPages.deployments.tab.loadOlder")}
          </Button>
          <span className="text-12 text-fg-muted">
            {t("appPages.deployments.tab.showing", { shown: formatCount(rows.length, t.locale), total: formatCount(total, t.locale) })}
          </span>
        </div>
      ) : rows.length > DEPLOYMENTS_PAGE ? (
        <p className="text-12 text-fg-muted">{t("appPages.deployments.tab.allShown", { total: formatCount(total, t.locale) })}</p>
      ) : null}
    </Section>
  );
}

/**
 * Every deploy of one app, newest first, each opening its build log; and beside it what the
 * app can go back to: its earlier versions, switched in seconds, or for an app kept in a single
 * folder, the backups a rollback restores.
 */
export function DeploymentsTab({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.deployments.tab.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));

  return (
    <div className="flex min-w-0 flex-col gap-8 lg:flex-row lg:items-start">
      <div className="min-w-0 flex-1">
        <History domain={domain} t={t} />
      </div>
      {app.data ? (
        <div className="min-w-0 lg:w-80 lg:shrink-0">
          <ReleasesSection domain={domain} layout={app.data.layout} />
        </div>
      ) : null}
    </div>
  );
}
