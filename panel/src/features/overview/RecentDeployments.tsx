import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Rocket } from "lucide-react";
import { useMemo } from "react";

import { deploymentsQuery } from "../../api/queries/deployments";
import { DeployStatePill } from "../../components/page/AppStatePill";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDuration } from "../../lib/format";
import type { Deployment } from "../apps/data";
import { deployMoment } from "../apps/data";

/** How many deploys the overview shows; the rest are on each app's Deployments tab. */
export const RECENT_COUNT = 8;

const LINK = "rounded-[4px] hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

function triggerLabel(t: T, triggeredBy: string): string {
  switch (triggeredBy) {
    case "panel":
      return t("overview.deployments.triggerConsole");
    case "cli":
      return t("overview.deployments.triggerCli");
    case "webhook":
      return t("overview.deployments.triggerWebhook");
    default:
      return triggeredBy;
  }
}

function columnsFor(t: T): readonly Column<Deployment>[] {
  return [
    {
      id: "status",
      header: t("overview.deployments.columnStatus"),
      width: "w-32",
      cell: (row) => <DeployStatePill status={row.status} appearance="inline" size="sm" />,
    },
    {
      id: "app",
      header: t("overview.deployments.columnApplication"),
      cell: (row) => (
        <Link to="/apps/$domain" params={{ domain: row.domain }} className={`${LINK} font-medium text-fg`}>
          {row.domain}
        </Link>
      ),
    },
    {
      id: "commit",
      header: t("overview.deployments.columnCommit"),
      cell: (row) => (
        <div className="flex min-w-0 flex-col py-1.5 leading-4">
          <Link
            to="/apps/$domain/deployments/$id"
            params={{ domain: row.domain, id: String(row.id) }}
            className={`${LINK} mono text-12 text-fg-muted`}
          >
            <span className="text-fg">{row.git_commit?.slice(0, 7) ?? `#${String(row.id)}`}</span>
            {row.git_branch ? <span className="text-fg-faint">{` ${row.git_branch}`}</span> : null}
            <span className="sr-only">{t("overview.deployments.deployOf", { id: row.id, domain: row.domain })}</span>
          </Link>
          {row.commit_message ? (
            <span title={row.commit_message} className="hidden max-w-[18rem] truncate text-12 text-fg-faint lg:block">
              {row.commit_message}
            </span>
          ) : null}
        </div>
      ),
    },
    {
      id: "trigger",
      header: t("overview.deployments.columnTrigger"),
      hideBelow: "md",
      cell: (row) => <span className="text-fg-muted">{triggerLabel(t, row.triggered_by)}</span>,
    },
    {
      id: "when",
      header: t("overview.deployments.columnWhen"),
      cell: (row) => <RelativeTime value={deployMoment(row)} className="text-fg-muted" />,
    },
    {
      id: "duration",
      header: t("overview.deployments.columnDuration"),
      align: "end",
      mono: true,
      hideBelow: "sm",
      cell: (row) =>
        row.duration_s === null || row.duration_s === undefined ? (
          <span className="text-fg-faint">{row.status === "running" ? t("overview.deployments.running") : "-"}</span>
        ) : (
          <span className="text-fg-muted">{formatDuration(row.duration_s)}</span>
        ),
    },
  ];
}

/**
 * The latest deploys across every app. Kept live by the `job` events: a deploy starting or
 * ending refreshes the history, so a new row appears and a running one settles by itself.
 */
export function RecentDeployments() {
  const t = useT();
  const deploys = useQuery(deploymentsQuery({ limit: RECENT_COUNT }));
  const columns = useMemo(() => columnsFor(t), [t]);
  return (
    <Section
      title={t("overview.deployments.title")}
      actions={
        <Link to="/activity" className={`${LINK} text-13 font-medium text-accent-fg`}>
          {t("overview.deployments.allActivity")}
        </Link>
      }
    >
      {deploys.isError && deploys.data === undefined ? (
        <ErrorBlock
          error={deploys.error}
          title={t("overview.deployments.couldNotLoad")}
          onRetry={() => void deploys.refetch()}
          retrying={deploys.isRefetching}
        />
      ) : (
        <DataTable
          columns={columns}
          rows={deploys.data?.items ?? []}
          getRowId={(row) => String(row.id)}
          caption={t("overview.deployments.caption")}
          loading={deploys.isPending}
          density="compact"
          empty={
            <EmptyState
              icon={<Rocket />}
              title={t("overview.deployments.emptyTitle")}
              description={t("overview.deployments.emptyDescription")}
              className="border-0 py-8"
            />
          }
        />
      )}
    </Section>
  );
}
