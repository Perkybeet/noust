import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "../ui/Button";
import { DataTable } from "../ui/DataTable";
import type { Column, DataTableProps } from "../ui/DataTable";
import { EmptyState } from "../ui/EmptyState";
import { Select } from "../ui/Select";
import { FilterBar } from "./FilterBar";
import type { FilterBarSearch } from "./FilterBar";
import { answerOf } from "./fleetServers";
import type { FleetServer } from "./fleetServers";
import { ServerCell } from "./ServerCell";

/** The value of "All servers": a select treats an empty value as no choice and shows its placeholder. */
const EVERY = "@every";

type TableProps<T> = Pick<
  DataTableProps<T>,
  "columns" | "getRowId" | "caption" | "onRowActivate" | "rowActions" | "loading" | "skeletonRows" | "empty" | "pagination" | "sort" | "defaultSort" | "onSortChange" | "mobile" | "layout" | "density"
>;

export interface FleetListProps<T> extends TableProps<T> {
  /**
   * `all`: the rows of several servers, so the server is a column and a filter. `server`: the rows of one, which
   * have neither: the same list, with everything else it has. A lone server draws `server` and nobody sees the difference.
   */
  scope: "all" | "server";
  /**
   * The servers that have what the list shows, each with how it answered: the filter's options, the name each row
   * wears and the mark of the rows of one that did not answer now. A server that gave nothing, or whose Noust does
   * not know the view, has no rows and is not an option (`ScopeLine` says why it is missing).
   */
  servers: readonly FleetServer[];
  /** The rows, after the page's own filters; the server filter is this piece's. `undefined`: not loaded yet. */
  rows: readonly T[] | undefined;
  /** The name of the server a row came from. */
  serverOf: (row: T) => string;
  /** Names the search landmark: "Filter backups". */
  filterLabel: string;
  /** The search box, its text kept by the page (in the address: `q`). */
  search?: FilterBarSearch;
  /** The page's own filters (a state, a type) as selects, drawn after the server's. */
  filters?: ReactNode;
  /** The count at the right ("3 of 17 databases"), from how many rows are shown and how many there are before the server filter. */
  count?: (shown: number, total: number) => ReactNode;
  /** Secondary actions for the list, at the right of the bar. Never the primary one. */
  actions?: ReactNode;
  /**
   * The server chosen in the filter, when the page keeps it (in the address, so a filtered list can be shared):
   * null is every server. Without it the piece keeps the choice itself.
   */
  server?: string | null;
  onServerChange?: (server: string | null) => void;
  className?: string;
}

/**
 * The table of always, for rows that live on several servers: with All servers the server is a column right after
 * what each row is (it sorts) and a filter that counts what each server has, and the rows of a server that did not
 * answer now say so, with how old they are. With one server the list loses both and keeps everything else, so
 * the page is one component for both scopes. Not for a list of servers (`ServersTable`) and not for a list nobody can
 * tell apart by server.
 */
export function FleetList<T>({
  scope,
  servers,
  rows,
  serverOf,
  filterLabel,
  search,
  filters,
  count,
  actions,
  server,
  onServerChange,
  columns,
  empty,
  className,
  ...table
}: FleetListProps<T>) {
  const t = useT();
  const [own, setOwn] = useState<string | null>(null);
  const chosen = server !== undefined ? server : own;
  const choose = (next: string | null): void => {
    if (server === undefined) setOwn(next);
    onServerChange?.(next);
  };
  const all = scope === "all";
  const named = useMemo(() => new Map(servers.map((entry) => [entry.name, entry])), [servers]);
  const shown = useMemo(() => (rows === undefined || !all || chosen === null ? rows : rows.filter((row) => serverOf(row) === chosen)), [rows, all, chosen, serverOf]);

  const withServer = useMemo<readonly Column<T>[]>(() => {
    const [first, ...rest] = columns;
    if (!all || first === undefined) return columns;
    const column: Column<T> = {
      id: "server",
      header: t("fleetCompare.names.server"),
      card: "meta",
      width: "w-44",
      sortValue: (row) => serverOf(row),
      cell: (row) => <ServerCell server={named.get(serverOf(row)) ?? { name: serverOf(row) }} />,
      stacked: (row) => answerOf(named.get(serverOf(row)) ?? { name: "" }).kind === "stale",
    };
    // The thing first, where it is next, then the rest: identity first (docs/DESIGN.md 6.8).
    return [first, column, ...rest];
  }, [columns, all, named, serverOf, t]);

  // What each server has of the rows the page left, so the filter says where to look before it is used.
  const options = useMemo(() => {
    const perServer = new Map<string, number>();
    for (const row of rows ?? []) perServer.set(serverOf(row), (perServer.get(serverOf(row)) ?? 0) + 1);
    const names = servers.filter((entry) => ["fresh", "stale"].includes(answerOf(entry).kind)).map((entry) => entry.name);
    // A server chosen in a shared link that the list does not know any more still shows as chosen.
    const listed = chosen !== null && !names.includes(chosen) ? [...names, chosen] : names;
    return [
      { value: EVERY, label: t("fleetCompare.list.everyServer", { count: rows?.length ?? 0 }) },
      ...listed.map((name) => ({ value: name, label: t("fleetCompare.list.serverOption", { server: name, count: perServer.get(name) ?? 0 }) })),
    ];
  }, [rows, servers, serverOf, chosen, t]);

  const nothingHere =
    all && chosen !== null && rows !== undefined && rows.length > 0 && shown?.length === 0 ? (
      <EmptyState
        variant="inline"
        title={t("fleetCompare.list.nothingFrom", { server: chosen })}
        action={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              choose(null);
            }}
          >
            {t("fleetCompare.list.showEvery")}
          </Button>
        }
      />
    ) : (
      empty
    );

  return (
    <div className={cx("flex min-w-0 flex-col gap-4", className)}>
      <FilterBar
        label={filterLabel}
        {...(search !== undefined ? { search } : {})}
        {...(all || filters !== undefined
          ? {
              filters: (
                <>
                  {all ? (
                    <Select
                      aria-label={t("fleetCompare.names.server")}
                      value={chosen ?? EVERY}
                      onValueChange={(next) => {
                        choose(next === EVERY ? null : next);
                      }}
                      options={options}
                      mono={false}
                    />
                  ) : null}
                  {filters}
                </>
              ),
            }
          : {})}
        {...(count !== undefined && shown !== undefined && rows !== undefined ? { count: count(shown.length, rows.length) } : {})}
        {...(actions !== undefined ? { actions } : {})}
      />
      <DataTable<T> {...table} columns={withServer} rows={shown} {...(nothingHere !== undefined ? { empty: nothingHere } : {})} />
    </div>
  );
}
