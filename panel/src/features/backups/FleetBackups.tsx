import { Play } from "lucide-react";
import { useMemo } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { textLinkClassName } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { formatBytes, parseTimestamp } from "../../lib/format";
import { useFleetActions } from "../fleet/BulkActionDialog";
import { field, numberOf, objectOf, originOf } from "../fleet/data";
import type { FleetRow, NodeOutcome } from "../fleet/data";
import { isFiltered, matchesQuery, patchSearch } from "../fleet/filters";
import type { FleetSearch, FleetSearchPatch } from "../fleet/filters";
import { HrefLink } from "../fleet/links";
import { ServerCell, StateFilter } from "../fleet/parts";
import { ServerScope } from "../operation/ServerScope";
import { ViewNotices } from "../operation/ViewNotices";
import { useView } from "../operation/views";

/** A backup older than this many days is a gap: the application has changed since. */
export const OLD_BACKUP_DAYS = 7;

export type FleetCoverage = "none" | "failed" | "old" | "unverified" | "ok";

const COVERAGE_VIEW: Readonly<Record<FleetCoverage, { state: Status; label: PlainKey }>> = {
  none: { state: "failed", label: "fleet.backups.coverage.none" },
  failed: { state: "failed", label: "fleet.backups.coverage.failed" },
  old: { state: "warning", label: "fleet.backups.coverage.old" },
  unverified: { state: "warning", label: "fleet.backups.coverage.unverified" },
  ok: { state: "running", label: "fleet.backups.coverage.ok" },
};

function lastBackupAt(row: FleetRow): string | null {
  const last = objectOf(row["last_backup"]);
  return last === null ? null : typeof last["timestamp"] === "string" ? last["timestamp"] : null;
}

/** An application's backups, as a gap or not: none, one that failed its check, an old one, one never checked, fine. */
export function rowCoverage(row: FleetRow, now: Date = new Date()): FleetCoverage {
  if ((numberOf(row["backups"]) ?? 0) === 0 || row["last_backup"] === null) return "none";
  if (row["verified"] === "failed") return "failed";
  const at = parseTimestamp(lastBackupAt(row));
  if (at !== null && now.getTime() - at.getTime() > OLD_BACKUP_DAYS * 86_400_000) return "old";
  if (row["verified"] === "never") return "unverified";
  return "ok";
}

const NAME_LINK = textLinkClassName("inline", "min-w-0", "row");

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>, withServer: boolean): Column<FleetRow>[] {
  return [
    {
      id: "app",
      header: t("fleet.column.app"),
      card: "title",
      sortValue: (row) => field(row, "domain"),
      cell: (row) => (
        <HrefLink href={originOf(row).href} className={NAME_LINK}>
          <span translate="no" className="truncate">
            {field(row, "domain") ?? ""}
          </span>
        </HrefLink>
      ),
    },
    {
      id: "coverage",
      header: t("fleet.column.state"),
      card: "status",
      width: "w-44",
      cell: (row) => {
        const view = COVERAGE_VIEW[rowCoverage(row)];
        return <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />;
      },
    },
    ...(withServer
      ? [
          {
            id: "server",
            header: t("fleet.column.server"),
            card: "meta" as const,
            sortValue: (row: FleetRow) => originOf(row).node,
            cell: (row: FleetRow) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
          },
        ]
      : []),
    {
      id: "last",
      header: t("fleet.column.lastBackup"),
      card: "meta",
      sortValue: (row) => lastBackupAt(row),
      cell: (row) => {
        const at = lastBackupAt(row);
        return at === null ? <EmptyCell reason={t("fleet.backups.never")} /> : <RelativeTime value={at} className="text-13 text-fg" />;
      },
    },
    {
      id: "size",
      header: t("fleet.column.size"),
      align: "end",
      hideBelow: "md",
      card: "meta",
      sortValue: (row) => numberOf(row["size"]),
      cell: (row) => {
        const size = numberOf(row["size"]);
        return size === null || size === 0 ? <EmptyCell reason={t("fleet.backups.noSize")} /> : <span className="text-13 text-fg tabular-nums">{formatBytes(size, t.locale)}</span>;
      },
    },
    {
      id: "schedule",
      header: t("fleet.column.schedule"),
      hideBelow: "lg",
      card: "meta",
      cell: (row) => <span className="text-13 text-fg">{row["scheduled"] === true ? t("fleet.backups.scheduled") : t("fleet.backups.unscheduled")}</span>,
    },
  ];
}

export interface FleetBackupsProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every application's backups across the servers (T1), gaps first: an application with none, one
 * whose last check failed, an old one, one never checked. It is the page of Backups with the
 * servers' column and their filter; each row opens that application's backups on its server, where
 * its archives are, and what is done to all of them (back up, check) is one action in the header.
 */
export function FleetBackups({ search, onSearchChange }: FleetBackupsProps) {
  const t = useT();
  const view = useView("backups", { nodes: search.server !== undefined ? [search.server] : [] });
  const actions = useFleetActions();
  const rows = useMemo(() => view.data?.items ?? [], [view.data]);
  const shown = rows.filter((row) => (search.state === undefined || rowCoverage(row) === search.state) && matchesQuery([field(row, "domain"), originOf(row).node], search.q));

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

  let content;
  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  if (view.isError && view.data === undefined) {
    content = <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  } else if (view.data !== undefined && rows.length === 0 && !isFiltered(search)) {
    content = <EmptyState variant="firstUse" icon={<OBJECT_ICONS.backup />} title={t("fleet.backups.empty")} description={t("backups.coverage.empty.description")} command="noust backup list" />;
  } else {
    content = (
      <DataTable
        caption={t("fleet.backups.caption")}
        columns={columns(t, view.outcomes, view.data?.fleet === true)}
        rows={shown}
        getRowId={(row) => `${originOf(row).node}:${field(row, "domain") ?? ""}`}
        loading={view.isPending}
        skeletonRows={6}
        pagination="fleet-backups"
        empty={
          <EmptyState
            variant="inline"
            title={t("fleet.filters.noMatch")}
            action={
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  onSearchChange({});
                }}
              >
                {t("common.words.clearFilters")}
              </Button>
            }
          />
        }
      />
    );
  }

  return (
    <ListPage
      header={{
        title: t("backups.page.title"),
        description: t("backups.page.description"),
        secondaryActions: (
          <Button
            onClick={() => {
              actions.open({ action: "backups_verify" });
            }}
          >
            {t("fleet.backups.verify")}
          </Button>
        ),
        primaryAction: (
          <Button
            variant="primary"
            icon={<Play aria-hidden="true" />}
            onClick={() => {
              actions.open({ action: "backups_run" });
            }}
          >
            {t("common.words.backUpNow")}
          </Button>
        ),
      }}
      notice={<ViewNotices view={view.data} resource="backups" />}
      filters={
        <FilterBar
          label={t("fleet.backups.filterLabel")}
          search={{
            value: search.q ?? "",
            onChange: (value) => {
              set({ q: value }, true);
            },
            label: t("fleet.backups.searchLabel"),
            placeholder: t("fleet.backups.searchPlaceholder"),
          }}
          filters={
            <>
              <ServerScope
                view={view.data}
                value={search.server}
                onChange={(server) => {
                  set({ server });
                }}
              />
              <StateFilter
                value={search.state}
                onChange={(state) => {
                  set({ state });
                }}
                options={(Object.keys(COVERAGE_VIEW) as FleetCoverage[]).map((coverage) => ({ value: coverage, label: t(COVERAGE_VIEW[coverage].label) }))}
              />
            </>
          }
          count={view.data === undefined ? "" : isFiltered(search) ? t("fleet.backups.countFiltered", { shown: shown.length, total: rows.length }) : t("fleet.backups.count", { count: rows.length })}
        />
      }
      footer={<CommandHint label={t("common.words.fromTerminal")} command="noust fleet backups" />}
    >
      {content}
    </ListPage>
  );
}
