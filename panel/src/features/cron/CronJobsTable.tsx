import type { ReactNode } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { STATE_RANK } from "../../components/page/status";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { parseTimestamp } from "../../lib/format";
import type { CronJob } from "./data";
import { calendarWords, runStatus } from "./data";

export interface CronJobsTableProps {
  jobs: readonly CronJob[];
  caption: string;
  loading?: boolean;
  empty?: ReactNode;
  onRowActivate?: (job: CronJob) => void;
  rowActions?: (job: CronJob) => ReactNode;
  className?: string;
}

/**
 * Every cron job: its name and command, how its last run ended (its state), its schedule in
 * words and as written, and its next run. A disabled job says so where its next run would be,
 * in the stopped grey with its ring, so it is told apart at a glance (item 56).
 */
export function CronJobsTable({ jobs, caption, loading = false, empty, onRowActivate, rowActions, className }: CronJobsTableProps) {
  const t = useT();
  const columns: Column<CronJob>[] = [
    {
      id: "name",
      header: t("cron.table.columns.job"),
      cell: (row) => (
        <span className="flex min-w-0 flex-col">
          <Mono className="font-medium">{row.name}</Mono>
          <Mono tone="faint" truncate title={row.command} className="max-w-80 text-12 font-normal">
            {row.command}
          </Mono>
        </span>
      ),
      sortValue: (row) => row.name,
    },
    {
      id: "last_result",
      header: t("cron.table.columns.lastResult"),
      width: "w-40",
      card: "status",
      cell: (row) => {
        const view = runStatus(row.last_result);
        return (
          <span className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
            <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />
            {view.detail !== undefined ? (
              <Mono tone="faint" className="text-12">
                {view.detail}
              </Mono>
            ) : null}
          </span>
        );
      },
      sortValue: (row) => STATE_RANK[runStatus(row.last_result).state],
    },
    {
      id: "schedule",
      header: t("cron.table.columns.schedule"),
      cell: (row) => (
        <span className="inline-flex min-w-0 flex-col align-top">
          <span className="text-fg">{calendarWords(row.schedule, row.on_calendar, t.locale)}</span>
          {/* The expression as written, beside its words on a desktop; a phone's card keeps the words. */}
          <Mono tone="faint" truncate className="text-12 max-sm:hidden">
            {row.on_calendar}
          </Mono>
        </span>
      ),
      sortValue: (row) => row.schedule,
    },
    {
      id: "next_run",
      header: t("cron.table.columns.nextRun"),
      width: "w-36",
      cell: (row) =>
        row.enabled ? (
          <RelativeTime value={row.next_run} fallback={row.next_run} />
        ) : (
          <StatusPill state="stopped" label={t("cron.table.disabled")} appearance="inline" size="sm" />
        ),
      sortValue: (row) => (row.enabled ? (parseTimestamp(row.next_run)?.getTime() ?? null) : null),
    },
  ];

  return (
    <DataTable
      mobile="cards"
      columns={columns}
      rows={jobs}
      getRowId={(row) => row.name}
      caption={caption}
      loading={loading}
      {...(empty !== undefined ? { empty } : {})}
      {...(onRowActivate ? { onRowActivate } : {})}
      {...(rowActions ? { rowActions } : {})}
      defaultSort={{ column: "name", direction: "ascending" }}
      {...(className !== undefined ? { className } : {})}
    />
  );
}
