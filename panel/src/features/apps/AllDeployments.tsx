import { DeployStatePill } from "../../components/page/AppStatePill";
import { RelativeTime } from "../../components/page/RelativeTime";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDuration } from "../../lib/format";
import { deploymentStatusWord, shortCommit, triggerWords } from "../app/deployments/words";
import type { Deployment } from "./data";

function columnsOf(t: T): Column<Deployment>[] {
  return [
    {
      id: "app",
      header: t("apps.deployments.columnApplication"),
      card: "title",
      sortValue: (row) => row.domain,
      cell: (row) => (
        <TextLink variant="row" to="/apps/$domain/deployments/$id" params={{ domain: row.domain, id: String(row.id) }} translate="no" className="-mx-1 w-fit max-w-full truncate px-1 py-0.5">
          {row.domain}
        </TextLink>
      ),
    },
    {
      id: "state",
      header: t("apps.deployments.columnState"),
      width: "w-40",
      card: "status",
      cell: (row) => <DeployStatePill status={deploymentStatusWord(row)} appearance="inline" size="sm" />,
    },
    {
      id: "change",
      header: t("apps.deployments.columnChange"),
      cell: (row) => (
        <span className="flex min-w-0 items-baseline gap-2">
          <Mono tone="muted">{`#${String(row.id)}`}</Mono>
          <span title={row.commit_message ?? undefined} className="truncate">
            {row.commit_message ?? shortCommit(row.git_commit) ?? t("apps.deployments.noChange")}
          </span>
        </span>
      ),
    },
    {
      id: "who",
      header: t("appPages.deployments.fields.startedBy"),
      hideBelow: "md",
      card: "hidden",
      cell: (row) => <span className="text-fg-muted">{row.started_by ?? triggerWords(t, row.triggered_by).label}</span>,
    },
    {
      id: "when",
      header: t("apps.deployments.columnWhen"),
      width: "w-32",
      sortValue: (row) => -(Date.parse(row.started_at ?? "") || 0),
      cell: (row) => <RelativeTime value={row.started_at} className="text-fg-muted" />,
    },
    {
      id: "took",
      header: t("apps.deployments.columnTook"),
      align: "end",
      mono: true,
      width: "w-28",
      hideBelow: "sm",
      cell: (row) => (row.duration_s === null || row.duration_s === undefined ? <EmptyCell reason={t("apps.deployments.notFinished")} /> : formatDuration(row.duration_s, t.locale)),
    },
  ];
}

/**
 * The deployments of every application, the newest first: the view of the Applications column that answers "what was
 * deployed lately, and did it work". The history of one application is its own section; this is the same rows across all
 * of them, so a failure is seen without opening each.
 */
export function AllDeployments({ deployments, loading }: { deployments: readonly Deployment[] | undefined; loading: boolean }) {
  const t = useT();
  return (
    <DataTable
      columns={columnsOf(t)}
      rows={deployments}
      loading={loading}
      skeletonRows={8}
      getRowId={(row) => String(row.id)}
      caption={t("apps.deployments.caption")}
      defaultSort={{ column: "when", direction: "ascending" }}
      pagination="deployments-of-all"
      empty={<EmptyState variant="inline" title={t("apps.deployments.empty")} />}
    />
  );
}
