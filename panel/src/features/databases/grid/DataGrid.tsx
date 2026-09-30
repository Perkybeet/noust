import { ArrowDown, ArrowUp, ChevronsUpDown, KeyRound } from "lucide-react";
import type { ReactNode } from "react";

import { Card } from "../../../components/ui/Card";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatCount } from "../../../lib/format";
import { Cell, isNumericKind } from "./Cell";

export interface GridColumn {
  name: string;
  /** The type as the engine spells it, under the name. */
  type?: string | undefined;
  /** How its cells are drawn: `numeric` at the right. */
  kind?: string | undefined;
  /** Its place in the primary key, from 1. */
  primaryKey?: number | null | undefined;
}

export interface GridRow {
  id: string;
  cells: readonly unknown[];
  /** Indexes of the cells the engine cut. */
  truncated?: readonly number[] | undefined;
}

export interface GridSort {
  column: string;
  descending: boolean;
}

export interface DataGridProps {
  /** Names the grid for assistive technology: "Rows of public.orders". */
  label: string;
  columns: readonly GridColumn[];
  rows: readonly GridRow[];
  /** The order the server sorted by; its header says so. */
  sort?: GridSort | null;
  /** Makes each header a button that asks the server for that order. */
  onSort?: (column: string) => void;
  /** Opens a row (its detail, its editor): the row number becomes a button. */
  onRowOpen?: (row: GridRow) => void;
  /** Per-row controls at the end, usually a menu. */
  rowActions?: (row: GridRow) => ReactNode;
  /** The number of the first row, for a page after the first. */
  offset?: number;
  loading?: boolean;
  skeletonRows?: number;
  /** Shown in the rows' place when there are none. */
  empty?: ReactNode;
  className?: string;
}

/**
 * Rows of a database's values, dense and scrollable both ways inside itself: a numbered gutter,
 * the column names with their types and primary-key marks in a header that stays in view, and
 * each value drawn by what it is (Cell). The page never scrolls sideways for it.
 */
export function DataGrid({
  label,
  columns,
  rows,
  sort = null,
  onSort,
  onRowOpen,
  rowActions,
  offset = 0,
  loading = false,
  skeletonRows = 8,
  empty,
  className,
}: DataGridProps) {
  const t = useT();
  const span = columns.length + 1 + (rowActions ? 1 : 0);
  return (
    <Card padding="none" as="div" {...(className !== undefined ? { className } : {})}>
      <div
        role="region"
        aria-label={label}
        tabIndex={0}
        className="relative max-h-editor min-w-0 overflow-auto rounded-card scroll-thin -outline-offset-2"
      >
        <table
          aria-busy={loading || undefined}
          className="w-max min-w-full border-collapse text-left text-12"
        >
          <caption className="sr-only">{label}</caption>
          <thead className="sticky top-0 bg-bg-sunken">
            <tr className="border-b border-border">
              <th
                scope="col"
                className="h-12 w-12 border-r border-border px-2 text-right font-medium text-fg-faint"
              >
                <span className="sr-only">{t("databases.grid.rowNumber")}</span>
                #
              </th>
              {columns.map((column) => {
                const sorted =
                  sort?.column === column.name
                    ? sort.descending
                      ? "descending"
                      : "ascending"
                    : undefined;
                const numeric = isNumericKind(column.kind);
                const heading = (
                  <span
                    className={cx(
                      "flex min-w-0 flex-col gap-0.5 py-1.5",
                      numeric && "items-end",
                    )}
                  >
                    <span className="flex items-center gap-1 font-medium text-fg">
                      {column.primaryKey != null ? (
                        <KeyRound
                          aria-label={t("databases.grid.primaryKey")}
                          className="size-icon-xs shrink-0 text-fg-muted"
                        />
                      ) : null}
                      <Mono tone="default">{column.name}</Mono>
                    </span>
                    {column.type ? (
                      <Mono tone="faint" className="font-normal">
                        {column.type}
                      </Mono>
                    ) : null}
                  </span>
                );
                return (
                  <th
                    key={column.name}
                    scope="col"
                    {...(sorted !== undefined ? { "aria-sort": sorted } : {})}
                    className={cx(
                      "h-12 max-w-80 px-3 align-bottom whitespace-nowrap",
                      numeric && "text-right",
                    )}
                  >
                    {onSort ? (
                      <button
                        type="button"
                        onClick={() => onSort(column.name)}
                        className={cx(
                          "-mx-1.5 inline-flex cursor-pointer items-end gap-1.5 rounded-control px-1.5 hover:bg-surface-hover",
                          numeric && "flex-row-reverse",
                        )}
                      >
                        {heading}
                        <span className="pb-2">
                          {sorted === "ascending" ? (
                            <ArrowUp
                              aria-hidden="true"
                              className="size-icon-xs text-fg"
                            />
                          ) : sorted === "descending" ? (
                            <ArrowDown
                              aria-hidden="true"
                              className="size-icon-xs text-fg"
                            />
                          ) : (
                            <ChevronsUpDown
                              aria-hidden="true"
                              className="size-icon-xs text-fg-faint"
                            />
                          )}
                        </span>
                      </button>
                    ) : (
                      heading
                    )}
                  </th>
                );
              })}
              {rowActions ? (
                <th scope="col" className="sticky right-0 w-12 border-l border-border bg-bg-sunken px-2">
                  <span className="sr-only">{t("databases.grid.actions")}</span>
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from(
                  { length: Math.max(1, skeletonRows) },
                  (_, index) => (
                    <tr
                      key={`skeleton-${String(index)}`}
                      className="border-b border-border last:border-0"
                    >
                      <td className="h-8 border-r border-border px-2" />
                      {columns.map((column) => (
                        <td key={column.name} className="h-8 px-3">
                          <Skeleton className="h-3 w-24" />
                        </td>
                      ))}
                      {rowActions ? <td className="h-8" /> : null}
                    </tr>
                  ),
                )
              : null}
            {!loading && rows.length === 0 && empty !== undefined ? (
              <tr>
                <td colSpan={span} className="px-4 py-2">
                  {empty}
                </td>
              </tr>
            ) : null}
            {!loading
              ? rows.map((row, index) => {
                  const cut = new Set(row.truncated ?? []);
                  const number = formatCount(offset + index + 1, t.locale);
                  return (
                    <tr
                      key={row.id}
                      className="border-b border-border last:border-0 hover:bg-surface-hover"
                    >
                      <td className="h-8 border-r border-border px-2 text-right text-fg-faint">
                        {onRowOpen ? (
                          <button
                            type="button"
                            onClick={() => onRowOpen(row)}
                            aria-label={t("databases.grid.openRow", { number })}
                            className="-mx-1 cursor-pointer rounded-chip px-1 tabular-nums hover:text-fg hover:underline"
                          >
                            {number}
                          </button>
                        ) : (
                          <span className="tabular-nums">{number}</span>
                        )}
                      </td>
                      {columns.map((column, at) => (
                        <td
                          key={column.name}
                          className={cx(
                            "h-8 max-w-80 px-3 whitespace-nowrap",
                            isNumericKind(column.kind) && "text-right",
                          )}
                        >
                          <Cell
                            value={row.cells[at]}
                            kind={column.kind}
                            truncated={cut.has(at)}
                          />
                        </td>
                      ))}
                      {rowActions ? (
                        <td className="sticky right-0 h-8 border-l border-border bg-surface px-1 text-right">
                          {rowActions(row)}
                        </td>
                      ) : null}
                    </tr>
                  );
                })
              : null}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
