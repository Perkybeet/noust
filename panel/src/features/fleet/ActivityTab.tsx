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
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import type { AuditEntry } from "../activity/data";
import { auditActionLabel, auditResultStatus, describeActor, describeWho } from "../activity/data";
import { field, originOf } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { PartialNotice, ServerCell, ServerFilter, StateFilter, useFleetView } from "./parts";

const RESULTS = ["ok", "failed"] as const;

function entryOf(row: FleetRow): AuditEntry {
  // Each row is the server's own audit entry, labelled with its server: read as one.
  return row as unknown as AuditEntry;
}

function actorOf(t: T, row: FleetRow): { label: string; raw: string } {
  const entry = entryOf(row);
  if (entry.who !== null && entry.who !== undefined) return describeWho(t, entry.who, entry.actor);
  return describeActor(t, entry.actor);
}

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<FleetRow>[] {
  return [
    {
      id: "when",
      header: t("fleet.column.when"),
      card: "meta",
      width: "w-32",
      cell: (row) => <RelativeTime value={field(row, "timestamp")} className="text-13 text-fg-muted" />,
    },
    {
      id: "action",
      header: t("fleet.column.action"),
      card: "title",
      cell: (row) => {
        const resource = field(row, "resource");
        return (
          <span className="flex min-w-0 flex-col">
            <span className="text-13 font-medium text-fg">{auditActionLabel(t, field(row, "action") ?? "")}</span>
            {resource !== null ? (
              <Mono tone="muted" truncate className="text-12">
                {resource}
              </Mono>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "result",
      header: t("fleet.column.result"),
      card: "status",
      width: "w-32",
      cell: (row) => {
        const view = auditResultStatus(t, field(row, "result") ?? "");
        return <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />;
      },
    },
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "meta",
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
    {
      id: "actor",
      header: t("fleet.column.actor"),
      hideBelow: "md",
      card: "meta",
      cell: (row) => {
        const actor = actorOf(t, row);
        return actor.label === "" ? <EmptyCell reason={t("fleet.activity.noActor")} /> : <span className="text-13 text-fg">{actor.label}</span>;
      },
    },
  ];
}

function failed(row: FleetRow, t: T): boolean {
  return auditResultStatus(t, field(row, "result") ?? "").state === "failed";
}

export interface ActivityTabProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/**
 * What happened lately on every server (T1), newest first, each event named with its server.
 * Each server's own audit log decides what this operator may read of it: a server that does not
 * let them is said so in the partial notice, not hidden.
 */
export function ActivityTab({ search, onSearchChange }: ActivityTabProps) {
  const t = useT();
  const view = useFleetView("activity");
  const rows = view.data?.items ?? [];
  // Two events of one server can share a moment; their place in the answer tells them apart.
  const keys = new Map(rows.map((row, index) => [row, `${originOf(row).node}:${String(index)}`]));
  const shown = rows.filter((row) => {
    const origin = originOf(row);
    if (search.server !== undefined && origin.node !== search.server) return false;
    if (search.state === "failed" && !failed(row, t)) return false;
    if (search.state === "ok" && failed(row, t)) return false;
    return matchesQuery([field(row, "action"), auditActionLabel(t, field(row, "action") ?? ""), field(row, "resource"), field(row, "actor"), origin.node], search.q);
  });

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PartialNotice view={view.data} />
      <FilterBar
        label={t("fleet.activity.filterLabel")}
        search={{
          value: search.q ?? "",
          onChange: (value) => {
            set({ q: value }, true);
          },
          label: t("fleet.activity.searchLabel"),
          placeholder: t("fleet.activity.searchPlaceholder"),
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
              options={RESULTS.map((result) => ({ value: result, label: t(`fleet.activity.result.${result}`) }))}
            />
          </>
        }
        count={view.data === undefined ? "" : isFiltered(search) ? t("fleet.activity.countFiltered", { shown: shown.length, total: rows.length }) : t("fleet.activity.count", { count: rows.length })}
      />
      <DataTable
        caption={t("fleet.activity.caption")}
        columns={columns(t, view.outcomes)}
        rows={shown}
        getRowId={(row) => keys.get(row) ?? ""}
        loading={view.isPending}
        skeletonRows={8}
        density="compact"
        mobile="cards"
        empty={
          rows.length === 0 && view.data !== undefined ? (
            <EmptyState variant="inline" title={t("fleet.activity.empty")} />
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
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust fleet activity" />
    </div>
  );
}
