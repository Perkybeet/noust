import { CalendarClock, History, Play, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes, formatCount, parseTimestamp } from "../../lib/format";
import { calendarWords } from "../cron/data";
import { COVERAGE_RANK } from "./coverage";
import type { Coverage, CoverageRow } from "./coverage";
import type { BackupRow } from "./BackupsTable";

const STATE: Readonly<Record<Coverage, Status>> = { current: "running", stale: "warning", never: "warning" };

function coverageLabel(coverage: Coverage, t: T): string {
  if (coverage === "current") return t("backups.coverage.state.current");
  if (coverage === "stale") return t("backups.coverage.state.stale");
  return t("backups.coverage.state.never");
}

export interface CoverageActions {
  onOpen: (row: CoverageRow) => void;
  onBackUp: (domain: string) => void;
  onRestore: (backup: BackupRow) => void;
  onSchedule: (row: CoverageRow) => void;
}

function RowActions({ row, actions }: { row: CoverageRow; actions: CoverageActions }) {
  const t = useT();
  return (
    <Menu align="end" trigger={<IconButton label={t("backups.coverage.actionsFor", { domain: row.domain })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      {row.deployed ? (
        <MenuItem icon={<Play />} onClick={() => actions.onBackUp(row.domain)}>
          {t("backups.coverage.actions.backUpNow")}
        </MenuItem>
      ) : null}
      <MenuItem icon={<History />} onClick={() => actions.onOpen(row)}>
        {t("backups.coverage.actions.viewBackups")}
      </MenuItem>
      {row.latest !== null ? (
        <MenuItem icon={<RotateCcw />} onClick={() => row.latest !== null && actions.onRestore(row.latest)}>
          {t("backups.coverage.actions.restoreLatest")}
        </MenuItem>
      ) : null}
      {row.deployed ? (
        <>
          <MenuSeparator />
          <MenuItem icon={<CalendarClock />} onClick={() => actions.onSchedule(row)}>
            {row.schedule !== null ? t("backups.coverage.actions.editSchedule") : t("backups.coverage.actions.schedule")}
          </MenuItem>
        </>
      ) : null}
    </Menu>
  );
}

export interface CoverageTableProps extends CoverageActions {
  rows: readonly CoverageRow[];
  caption: string;
  loading?: boolean;
  skeletonRows?: number;
  empty?: ReactNode;
}

/**
 * Every application and whether it is protected: its state (up to date, out of date, never
 * backed up), when the newest backup was made, what schedules the next, where copies go, and
 * how many backups it has. A row opens that application's backups in a drawer.
 */
export function CoverageTable({ rows, caption, loading = false, skeletonRows, empty, ...actions }: CoverageTableProps) {
  const t = useT();
  const columns: Column<CoverageRow>[] = [
    {
      id: "domain",
      header: t("backups.coverage.columns.application"),
      cell: (row) =>
        row.deployed ? (
          row.domain
        ) : (
          <span className="flex min-w-0 flex-col">
            <span>{row.domain}</span>
            <span className="text-12 font-normal text-fg-muted">{t("backups.coverage.removed")}</span>
          </span>
        ),
      sortValue: (row) => row.domain,
    },
    {
      id: "state",
      header: t("backups.coverage.columns.state"),
      width: "w-36",
      card: "status",
      cell: (row) => <StatusPill state={STATE[row.coverage]} label={coverageLabel(row.coverage, t)} appearance="inline" size="sm" />,
      sortValue: (row) => COVERAGE_RANK[row.coverage],
    },
    {
      id: "latest",
      header: t("backups.coverage.columns.latest"),
      width: "w-32",
      cell: (row) => (row.latest !== null ? <RelativeTime value={row.latest.timestamp} /> : <span className="text-fg-muted">{t("time.never")}</span>),
      sortValue: (row) => (row.latest !== null ? (parseTimestamp(row.latest.timestamp)?.getTime() ?? null) : null),
    },
    {
      id: "schedule",
      header: t("backups.coverage.columns.schedule"),
      hideBelow: "md",
      cell: (row) => {
        if (row.schedule === null) return <span className="text-fg-muted">{t("backups.coverage.notScheduled")}</span>;
        // Where each backup is also copied, under when: only the rows that have one say it.
        const names = row.schedule.destinations?.map((destination) => destination.name) ?? [];
        return (
          <span className="inline-flex min-w-0 flex-col align-top">
            <span className="text-fg">{calendarWords(row.schedule.schedule, row.schedule.on_calendar, t.locale)}</span>
            {names.length > 0 ? (
              <span className="truncate text-12 text-fg-muted max-sm:hidden" title={names.join(", ")}>
                {t.rich("backups.coverage.copiedTo", { names: <Mono tone="muted">{names.join(", ")}</Mono> })}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "count",
      header: t("backups.coverage.columns.count"),
      align: "end",
      mono: true,
      width: "w-24",
      hideBelow: "sm",
      card: "hidden",
      cell: (row) => formatCount(row.backups.length, t.locale),
      sortValue: (row) => row.backups.length,
    },
    {
      id: "size",
      header: t("backups.coverage.columns.size"),
      align: "end",
      mono: true,
      width: "w-24",
      hideBelow: "sm",
      card: "hidden",
      cell: (row) => (row.backups.length > 0 ? formatBytes(row.size, t.locale) : <EmptyCell reason={t("backups.coverage.neverReason")} />),
      sortValue: (row) => row.size,
    },
  ];

  return (
    <DataTable
      mobile="cards"
      columns={columns}
      rows={rows}
      getRowId={(row) => row.domain}
      caption={caption}
      loading={loading}
      {...(skeletonRows !== undefined ? { skeletonRows } : {})}
      {...(empty !== undefined ? { empty } : {})}
      onRowActivate={actions.onOpen}
      rowActions={(row) => <RowActions row={row} actions={actions} />}
      defaultSort={{ column: "domain", direction: "ascending" }}
    />
  );
}
