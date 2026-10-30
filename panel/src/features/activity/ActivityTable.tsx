import type { ReactNode } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { STATE_RANK } from "../../components/page/status";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { IconButton } from "../../components/ui/IconButton";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { parseTimestamp } from "../../lib/format";
import { actionWords, actorSource, actorWords, detailOf, resourceWords, resultView, rowActor } from "./data";
import type { ActivityJob, ActivityRow } from "./data";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import type { NodeOutcome } from "../fleet/data";
import { ServerCell } from "../fleet/parts";

/**
 * Rows drawn while the timeline loads: more than a desktop screen holds. A history's first
 * page (up to 50 jobs and 30 audit entries) almost always runs past the fold, so what sits
 * under the table ("Load more", the terminal hint) starts below it and does not slide down
 * as the rows arrive; a short history only makes the table shorter.
 */
const SKELETON_ROWS = 20;

export interface ActivityTableProps {
  rows: readonly ActivityRow[];
  caption: string;
  loading?: boolean;
  empty?: ReactNode;
  /** Opens a job's captured log; audit rows have none, so they get no action at all. */
  onOpenJobLog: (job: ActivityJob) => void;
  /**
   * How each server answered, by name: a list of the events of several servers names the server of
   * each one in a column of its own. Left out, the list is one server's and draws none.
   */
  servers?: ReadonlyMap<string, NodeOutcome>;
  className?: string;
}

/**
 * The jobs history and the audit log, merged into one timeline: what happened and how it
 * ended, to what, who did it and from where, and when. Only a job row opens anything - see
 * `./data` for the merge and the words each column uses.
 */
export function ActivityTable({ rows, caption, loading = false, empty, onOpenJobLog, servers, className }: ActivityTableProps) {
  const t = useT();
  const columns: Column<ActivityRow>[] = [
    {
      id: "action",
      header: t("activity.columnAction"),
      card: "title",
      cell: (row) => {
        const words = actionWords(t, row);
        // An action with no words of its own is its name, once, as recorded.
        if (words.label === words.raw) return <Mono className="font-normal">{words.raw}</Mono>;
        return (
          <span className="inline-flex max-w-64 min-w-0 flex-col align-top">
            <span className="truncate text-fg">{words.label}</span>
            {/* On a phone the card keeps the words; the recorded name is the desktop's second line. */}
            <Mono tone="faint" truncate title={words.raw} className="text-12 font-normal max-sm:hidden">
              {words.raw}
            </Mono>
          </span>
        );
      },
      sortValue: (row) => actionWords(t, row).label,
    },
    {
      id: "result",
      header: t("activity.columnResult"),
      width: "w-40",
      card: "status",
      cell: (row) => {
        const view = resultView(t, row);
        return <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />;
      },
      sortValue: (row) => STATE_RANK[resultView(t, row).state],
    },
    {
      id: "resource",
      header: t("activity.columnResource"),
      hideBelow: "md",
      cell: (row) => {
        const { object, raw } = resourceWords(row);
        if (object === null) return <EmptyCell reason={t("activity.noResource")} />;
        return (
          <span className="block max-w-56">
            <Mono truncate title={raw ?? object}>
              {object}
            </Mono>
          </span>
        );
      },
      sortValue: (row) => resourceWords(row).object,
    },
    ...(servers === undefined
      ? []
      : [
          {
            id: "server",
            header: t("fleet.column.server"),
            width: "w-40",
            card: "meta" as const,
            cell: (row: ActivityRow) => (row.kind === "audit" && row.origin !== undefined ? <ServerCell origin={row.origin} outcome={servers.get(row.origin.node)} /> : null),
            sortValue: (row: ActivityRow) => (row.kind === "audit" ? (row.origin?.node ?? "") : ""),
          },
        ]),
    {
      id: "actor",
      header: t("activity.columnActor"),
      width: "w-48",
      hideBelow: "sm",
      cell: (row) => {
        const words = actorWords(t, row);
        // A job queued before jobs carried an actor: said quietly, with no raw value to show.
        if (rowActor(row) === null) return <span className="text-fg-muted">{words.label}</span>;
        const source = actorSource(row);
        return (
          <span className="inline-flex max-w-48 min-w-0 flex-col align-top">
            <span className="truncate text-fg">{words.label}</span>
            {/* The recorded label is a second line only when it says more than the words do. */}
            {words.label !== words.raw || source !== null ? (
              <Mono tone="faint" truncate title={words.raw} className="text-12 max-sm:hidden">
                {words.label === words.raw && source !== null ? source : words.raw}
              </Mono>
            ) : null}
          </span>
        );
      },
      sortValue: (row) => actorWords(t, row).label,
    },
    {
      id: "time",
      header: t("activity.columnTime"),
      width: "w-32",
      cell: (row) => <RelativeTime value={row.timestamp} />,
      sortValue: (row) => parseTimestamp(row.timestamp)?.getTime() ?? null,
    },
    {
      id: "detail",
      header: t("activity.columnDetail"),
      hideBelow: "lg",
      card: "hidden",
      cell: (row) => {
        if (row.kind === "audit" && row.secondFactor === true) {
          return <span className="block max-w-72 truncate text-fg-muted">{t("activity.withSecondFactor")}</span>;
        }
        const detail = detailOf(row);
        // What the server wrote, verbatim and untranslated: a system value, in mono.
        return detail === null ? (
          <EmptyCell reason={t("activity.noDetail")} />
        ) : (
          <span className="block max-w-72">
            <Mono tone="muted" truncate title={detail}>
              {detail}
            </Mono>
          </span>
        );
      },
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      caption={caption}
      loading={loading}
      skeletonRows={SKELETON_ROWS}
      rowActions={(row) =>
        row.kind === "job" ? (
          <IconButton label={t("activity.viewLogAria", { name: row.job.name })} icon={<OBJECT_ICONS.log />} size="sm" onClick={() => onOpenJobLog(row.job)} />
        ) : null
      }
      {...(empty !== undefined ? { empty } : {})}
      defaultSort={{ column: "time", direction: "descending" }}
      {...(className !== undefined ? { className } : {})}
    />
  );
}
