import { Play } from "lucide-react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { formatBytes, parseTimestamp } from "../../lib/format";
import { useFleetActions } from "./BulkActionDialog";
import { field, numberOf, objectOf, originOf } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { HrefLink } from "./links";
import { PartialNotice, ServerCell, ServerFilter, StateFilter, useFleetView } from "./parts";

/** A backup older than this many days is a gap: the application has changed since. */
export const OLD_BACKUP_DAYS = 7;

export type Coverage = "none" | "failed" | "old" | "unverified" | "ok";

const COVERAGE_VIEW: Readonly<Record<Coverage, { state: Status; label: PlainKey }>> = {
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

/** An application's backups, as a gap or not: none, one that failed its check, an old one, never checked, fine. */
export function coverageOf(row: FleetRow, now: Date = new Date()): Coverage {
  if ((numberOf(row["backups"]) ?? 0) === 0 || row["last_backup"] === null) return "none";
  if (row["verified"] === "failed") return "failed";
  const at = parseTimestamp(lastBackupAt(row));
  if (at !== null && now.getTime() - at.getTime() > OLD_BACKUP_DAYS * 86_400_000) return "old";
  if (row["verified"] === "never") return "unverified";
  return "ok";
}

const NAME_LINK = "min-w-0 rounded-chip font-medium text-fg hover:underline hover:underline-offset-2";

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<FleetRow>[] {
  return [
    {
      id: "app",
      header: t("fleet.column.app"),
      card: "title",
      sortValue: (row) => field(row, "domain"),
      cell: (row) => (
        <HrefLink href={originOf(row).href} className={NAME_LINK}>
          <Mono truncate>{field(row, "domain") ?? ""}</Mono>
        </HrefLink>
      ),
    },
    {
      id: "coverage",
      header: t("fleet.column.coverage"),
      card: "status",
      width: "w-44",
      cell: (row) => {
        const view = COVERAGE_VIEW[coverageOf(row)];
        return <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />;
      },
    },
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "meta",
      sortValue: (row) => originOf(row).node,
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
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

export interface BackupsTabProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every application's backups across the fleet (T1), gaps first: an application with none,
 * one whose last check failed, an old one, one never checked. Each row opens that application's
 * backups on its server, where its archives are.
 */
export function BackupsTab({ search, onSearchChange }: BackupsTabProps) {
  const t = useT();
  const view = useFleetView("backups");
  const actions = useFleetActions();
  const rows = view.data?.items ?? [];
  const shown = rows.filter((row) => {
    const origin = originOf(row);
    if (search.server !== undefined && origin.node !== search.server) return false;
    if (search.state !== undefined && coverageOf(row) !== search.state) return false;
    return matchesQuery([field(row, "domain"), origin.node], search.q);
  });
  const gaps = rows.filter((row) => coverageOf(row) === "none").length;
  const unscheduled = rows.filter((row) => row["scheduled"] !== true).length;

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  const count =
    view.data === undefined
      ? ""
      : isFiltered(search)
        ? t("fleet.backups.countFiltered", { shown: shown.length, total: rows.length })
        : [
            t("fleet.backups.count", { count: rows.length }),
            ...(gaps > 0 ? [t("fleet.backups.gaps", { count: gaps })] : []),
            ...(unscheduled > 0 ? [t("fleet.backups.unscheduledCount", { count: unscheduled })] : []),
          ].join(" · ");

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PartialNotice view={view.data} />
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
            <ServerFilter
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
              options={(Object.keys(COVERAGE_VIEW) as Coverage[]).map((coverage) => ({ value: coverage, label: t(COVERAGE_VIEW[coverage].label) }))}
            />
          </>
        }
        count={count}
        actions={
          <>
            <Button
              onClick={() => {
                actions.open({ action: "backups_verify" });
              }}
            >
              {t("fleet.backups.verify")}
            </Button>
            <Button
              icon={<Play aria-hidden="true" />}
              onClick={() => {
                actions.open({ action: "backups_run" });
              }}
            >
              {t("fleet.backups.backUp")}
            </Button>
          </>
        }
      />
      <DataTable
        caption={t("fleet.backups.caption")}
        columns={columns(t, view.outcomes)}
        rows={shown}
        getRowId={(row) => `${originOf(row).node}:${field(row, "domain") ?? ""}`}
        loading={view.isPending}
        skeletonRows={6}
        mobile="cards"
        empty={
          rows.length === 0 && view.data !== undefined ? (
            <EmptyState variant="inline" title={t("fleet.backups.empty")} />
          ) : (
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
                  {t("fleet.filters.clear")}
                </Button>
              }
            />
          )
        }
      />
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust fleet backups" />
    </div>
  );
}
