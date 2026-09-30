import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Pencil, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { relationQuery, rowsQuery } from "../../../api/queries/databases";
import type { RelationDetail, RowsParams, TableRow } from "../../../api/queries/databases";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { formatBytes, formatCount } from "../../../lib/format";
import { DataGrid } from "../grid/DataGrid";
import type { GridRow } from "../grid/DataGrid";
import { FilterRow } from "./FilterBarRow";
import { RowDrawer } from "./RowDrawer";
import type { RowDrawerMode } from "./RowDrawer";
import { DEFAULT_PAGE_SIZE, PAGE_SIZES, formatFilter, parseFilter, parseSort } from "./search";
import type { DataSearch, DataSearchPatch, Filter, PageSize } from "./search";
import { StructureView } from "./StructureView";

export interface TableViewProps {
  engine: string;
  name: string;
  schema: string;
  relation: string;
  search: DataSearch;
  onSearchChange: (patch: DataSearchPatch) => void;
}

/** A page of the table: where it starts (a keyset cursor or an offset), from the first. */
type PageToken = { cursor: string } | { offset: number } | null;

function rowId(row: TableRow, index: number): string {
  const key = Object.entries(row.key ?? {});
  return key.length > 0 ? key.map(([column, value]) => `${column}=${value}`).join("&") : `row-${String(index)}`;
}

/**
 * One table or view: its rows, a page at a time in the server's order, filtered by chips and
 * sorted by a click on a column; or its structure. A table with a primary key can have one row
 * edited, inserted or deleted (sudo mode, one exact row); one without is read-only, and says so.
 */
export function TableView({ engine, name, schema, relation, search, onSearchChange }: TableViewProps) {
  const t = useT();
  const detail = useQuery(relationQuery(engine, name, schema, relation));
  const limit: PageSize = search.limit ?? DEFAULT_PAGE_SIZE;
  const filters = useMemo(() => (search.filter ?? []).map(parseFilter).filter((item): item is Filter => item !== null), [search.filter]);
  const sort = parseSort(search.sort);
  // The page is local: a new order, filter or size starts again from the first page.
  const pageKey = `${search.sort ?? ""}|${(search.filter ?? []).join("|")}|${String(limit)}`;
  const [pages, setPages] = useState<{ key: string; stack: PageToken[] }>({ key: pageKey, stack: [null] });
  const stack = pages.key === pageKey ? pages.stack : [null];
  const token = stack[stack.length - 1] ?? null;
  const [counted, setCounted] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<{ mode: RowDrawerMode; row: TableRow | null } | null>(null);

  const params: RowsParams = {
    schema,
    relation,
    limit,
    ...(sort !== null ? { order: [`${sort.column}:${sort.descending ? "desc" : "asc"}`] } : {}),
    ...(filters.length > 0 ? { filter: filters.map(formatFilter) } : {}),
    ...(token !== null && "cursor" in token ? { cursor: token.cursor } : {}),
    ...(token !== null && "offset" in token ? { offset: token.offset } : {}),
    ...(counted === pageKey ? { count: true } : {}),
  };
  const rows = useQuery({ ...rowsQuery(engine, name, params), enabled: search.view !== "structure", placeholderData: keepPreviousData });

  const structure: RelationDetail | undefined = detail.data;
  const columns = rows.data?.columns ?? structure?.columns ?? [];
  const editable = rows.data?.editable ?? structure?.editable ?? false;
  const gridColumns = columns.map((column) => ({ name: column.name, type: column.type, kind: column.kind, primaryKey: column.primary_key ?? null }));
  const pageRows = rows.data?.rows ?? [];
  const gridRows: (GridRow & { source: TableRow })[] = pageRows.map((row, index) => ({ id: rowId(row, index), cells: row.cells, truncated: row.truncated, source: row }));
  const first = (stack.length - 1) * limit;

  const next = (): void => {
    const data = rows.data;
    if (!data?.has_more) return;
    const nextToken: PageToken = data.next_cursor != null ? { cursor: data.next_cursor } : data.next_offset != null ? { offset: data.next_offset } : null;
    if (nextToken === null) return;
    setPages({ key: pageKey, stack: [...stack, nextToken] });
  };
  const previous = (): void => {
    if (stack.length > 1) setPages({ key: pageKey, stack: stack.slice(0, -1) });
  };

  const toggleSort = (column: string): void => {
    const descending = sort?.column === column && !sort.descending;
    onSearchChange({ sort: `${column}:${descending ? "desc" : "asc"}` });
  };

  const viewSwitch = (
    <SegmentedControl
      label={t("databases.data.viewLabel")}
      value={search.view === "structure" ? "structure" : "rows"}
      onValueChange={(value) => onSearchChange({ view: value === "structure" ? "structure" : undefined })}
      options={[
        { value: "rows", label: t("databases.data.rows") },
        { value: "structure", label: t("databases.data.structure") },
      ]}
    />
  );

  const Add = Plus;
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <Mono tone="default" className="text-14 font-medium">{`${schema}.${relation}`}</Mono>
          {structure !== undefined ? (
            <>
              {structure.kind !== "table" ? <Badge>{t("databases.data.kindView")}</Badge> : null}
              <span className="text-12 text-fg-muted">
                {structure.rows_estimate != null ? t("databases.data.estimate", { rows: formatCount(structure.rows_estimate, t.locale) }) : null}
                {structure.rows_estimate != null && structure.size_bytes != null ? " · " : null}
                {structure.size_bytes != null ? formatBytes(structure.size_bytes, t.locale) : null}
              </span>
            </>
          ) : (
            <Skeleton className="h-3 w-32" />
          )}
        </div>
        <div className="flex items-center gap-2">
          {search.view !== "structure" && editable ? (
            <Button size="sm" icon={<Add aria-hidden="true" />} onClick={() => setDrawer({ mode: "insert", row: null })}>
              {t("databases.data.insertRow")}
            </Button>
          ) : null}
          {viewSwitch}
        </div>
      </div>

      {search.view === "structure" ? (
        detail.isError ? (
          <ErrorBlock error={detail.error} title={t("databases.data.structureFailed")} onRetry={() => void detail.refetch()} retrying={detail.isRefetching} />
        ) : (
          <StructureView detail={structure} />
        )
      ) : (
        <>
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <FilterRow
              columns={columns}
              filters={filters}
              onChange={(nextFilters) => onSearchChange({ filter: nextFilters.length > 0 ? nextFilters.map(formatFilter) : undefined })}
            />
            {!editable && rows.data !== undefined && structure?.kind === "table" ? (
              <span className="text-12 text-fg-muted">{t("databases.data.readOnlyNoKey")}</span>
            ) : null}
          </div>

          {rows.isError && rows.data === undefined ? (
            <ErrorBlock error={rows.error} title={t("databases.data.rowsFailed")} onRetry={() => void rows.refetch()} retrying={rows.isRefetching} />
          ) : (
            <DataGrid
              label={t("databases.data.rowsOf", { name: `${schema}.${relation}` })}
              columns={gridColumns}
              rows={gridRows}
              sort={sort}
              onSort={toggleSort}
              onRowOpen={(row) => setDrawer({ mode: "view", row: (row as GridRow & { source: TableRow }).source })}
              {...(editable
                ? {
                    rowActions: (row: GridRow) => {
                      const source = (row as GridRow & { source: TableRow }).source;
                      return (
                        <Menu align="end" trigger={<IconButton label={t("databases.data.rowActions")} icon={<ICONS.more />} size="sm" tooltip={false} />}>
                          <MenuItem icon={<Pencil />} onClick={() => setDrawer({ mode: "edit", row: source })}>
                            {t("databases.data.editRow")}
                          </MenuItem>
                          <MenuSeparator />
                          <MenuItem icon={<Trash2 />} destructive onClick={() => setDrawer({ mode: "delete", row: source })}>
                            {t("databases.data.deleteRow")}
                          </MenuItem>
                        </Menu>
                      );
                    },
                  }
                : {})}
              offset={first}
              loading={rows.data === undefined}
              // As many as a page holds: the grid is as tall as it will be once they arrive.
              skeletonRows={limit}
              empty={
                <EmptyState
                  variant="inline"
                  title={filters.length > 0 ? t("databases.data.noMatchingRows") : t("databases.data.noRows")}
                  {...(filters.length > 0
                    ? {
                        action: (
                          <Button size="sm" variant="ghost" onClick={() => onSearchChange({ filter: undefined })}>
                            {t("databases.data.clearFilters")}
                          </Button>
                        ),
                      }
                    : {})}
                />
              }
            />
          )}

          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 text-12 text-fg-muted">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span aria-live="polite">
                {pageRows.length === 0
                  ? t("databases.data.noRowsHere")
                  : t("databases.data.showing", { from: formatCount(first + 1, t.locale), to: formatCount(first + pageRows.length, t.locale) })}
              </span>
              {rows.data?.count != null ? (
                <span>{t("databases.data.matching", { count: rows.data.count })}</span>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => setCounted(pageKey)} disabled={rows.isFetching}>
                  {t("databases.data.countExactly")}
                </Button>
              )}
              {rows.data !== undefined ? (
                <span>{rows.data.pagination === "keyset" ? t("databases.data.byKey") : t("databases.data.byOffset")}</span>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              <Select
                size="sm"
                aria-label={t("databases.data.pageSize")}
                value={String(limit)}
                onValueChange={(value) => onSearchChange({ limit: Number(value) === DEFAULT_PAGE_SIZE ? undefined : (Number(value) as PageSize) })}
                options={PAGE_SIZES.map((size) => ({ value: String(size), label: t("databases.data.perPage", { count: size }) }))}
              />
              <Button size="sm" icon={<ChevronLeft aria-hidden="true" />} disabled={stack.length <= 1 || rows.isFetching} onClick={previous}>
                {t("databases.data.previous")}
              </Button>
              <Button size="sm" trailingIcon={<ChevronRight aria-hidden="true" />} disabled={!rows.data?.has_more || rows.isFetching} onClick={next}>
                {t("databases.data.next")}
              </Button>
            </div>
          </div>
        </>
      )}

      {drawer !== null ? (
        <RowDrawer
          engine={engine}
          name={name}
          schema={schema}
          relation={relation}
          columns={columns}
          editable={editable}
          mode={drawer.mode}
          row={drawer.row}
          onModeChange={(mode) => setDrawer({ ...drawer, mode })}
          onClose={() => setDrawer(null)}
        />
      ) : null}
    </div>
  );
}
