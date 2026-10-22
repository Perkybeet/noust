import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../../api/client";
import { useDocumentTitle } from "../../../app/documentTitle";
import { FilterBar } from "../../../components/page/FilterBar";
import { QueryState } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Badge } from "../../../components/ui/Badge";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { OperationDrawer, OperationFlags } from "./OperationDrawer";
import { METHODS, validateApiReferenceSearch } from "./search";
import type { ApiReferenceSearch } from "./search";
import { filterOperations, readDocument } from "./openapiModel";
import type { ApiOperation } from "./openapiModel";

const ALL = "__all";

/** A change to the filters; undefined takes one off. */
type SearchPatch = { [K in keyof ApiReferenceSearch]?: string | undefined };

export interface ApiReferenceProps {
  search: ApiReferenceSearch;
  onSearchChange: (search: ApiReferenceSearch, options?: { replace?: boolean }) => void;
}

function columnsFor(t: T): Column<ApiOperation>[] {
  return [
    { id: "method", header: t("apiReference.table.column.method"), card: "status", width: "w-24", cell: (operation) => <Badge mono>{operation.method}</Badge> },
    {
      id: "route",
      header: t("apiReference.table.column.route"),
      card: "title",
      cell: (operation) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <Mono tone="default">{operation.path}</Mono>
          <OperationFlags operation={operation} />
        </span>
      ),
    },
    { id: "summary", header: t("apiReference.table.column.summary"), hideBelow: "md", cell: (operation) => (
        <span translate="no" className="text-13 text-fg-muted">
          {operation.summary}
        </span>
      ),
    },
    {
      id: "permission",
      header: t("apiReference.table.column.permission"),
      hideBelow: "lg",
      cell: (operation) => (operation.permission === null ? <EmptyCell reason={t("apiReference.drawer.noPermission")} /> : <Mono tone="default">{operation.permission}</Mono>),
    },
  ];
}

function readApiDescription(source: unknown) {
  return readDocument(source);
}

/**
 * Settings > API reference: every route the server answers, read from its own OpenAPI document.
 * The same document the console is typed from and an integrator's client is generated from, so
 * this page cannot say a route exists that does not, nor leave one out. A route opens in a drawer
 * with what it needs, takes and answers, a curl to copy, and, for a read, a way to try it.
 */
export function ApiReference({ search, onSearchChange }: ApiReferenceProps) {
  const t = useT();
  useDocumentTitle(t("apiReference.documentTitle"), 1);
  const query = useQuery({
    queryKey: ["api", "openapi"],
    queryFn: ({ signal }) => api<unknown>("GET", "/api/openapi.json", undefined, { signal }),
    select: readApiDescription,
    staleTime: Infinity,
  });
  const [open, setOpen] = useState<ApiOperation | null>(null);
  const operations = useMemo(() => query.data?.operations ?? [], [query.data]);
  const shown = useMemo(() => filterOperations(operations, search), [operations, search]);
  const columns = columnsFor(t);
  const filtered = search.q !== undefined || search.tag !== undefined || search.method !== undefined;

  const set = (patch: SearchPatch, options?: { replace?: boolean }): void => {
    onSearchChange(validateApiReferenceSearch({ ...search, ...patch }), options);
  };

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-measure text-13 text-fg-muted">{t("apiReference.intro")}</p>
      <QueryState
        query={query}
        label={t("apiReference.loadingLabel")}
        skeleton={
          <div className="flex flex-col gap-3">
            <Skeleton className="h-10 w-full rounded-control" />
            <Skeleton className="h-64 w-full rounded-card" />
          </div>
        }
      >
        {(document) => (
          <>
            <FilterBar
              label={t("apiReference.filter.label")}
              search={{
                value: search.q ?? "",
                onChange: (value) => {
                  set({ q: value === "" ? undefined : value }, { replace: true });
                },
                label: t("apiReference.filter.searchLabel"),
                placeholder: t("apiReference.filter.searchPlaceholder"),
              }}
              filters={
                <>
                  <Select
                    aria-label={t("apiReference.filter.area")}
                    options={[{ value: ALL, label: t("apiReference.filter.allAreas") }, ...document.tags.map((tag) => ({ value: tag, label: tag }))]}
                    value={search.tag ?? ALL}
                    onValueChange={(value) => {
                      set({ tag: value === ALL ? undefined : value });
                    }}
                  />
                  <Select
                    aria-label={t("apiReference.filter.method")}
                    options={[{ value: ALL, label: t("apiReference.filter.allMethods") }, ...METHODS.map((method) => ({ value: method, label: method }))]}
                    value={search.method ?? ALL}
                    onValueChange={(value) => {
                      set({ method: value === ALL ? undefined : value });
                    }}
                    compact
                  />
                </>
              }
              count={t("apiReference.filter.count", { shown: shown.length, total: operations.length })}
            />
            {shown.length === 0 ? (
              <EmptyState
                variant="inline"
                title={t("apiReference.filter.nothing")}
                action={
                  filtered ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        onSearchChange({});
                      }}
                    >
                      {t("common.words.clearFilters")}
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <DataTable
                caption={t("apiReference.table.caption")}
                columns={columns}
                rows={shown}
                getRowId={(operation) => operation.id}
                pagination="api-reference"
                onRowActivate={(operation) => {
                  setOpen(operation);
                }}
              />
            )}
            {document.version !== "" ? <p className="text-12 text-fg-faint">{t("apiReference.version", { version: document.version })}</p> : null}
          </>
        )}
      </QueryState>
      {open !== null ? (
        <OperationDrawer
          operation={open}
          onClose={() => {
            setOpen(null);
          }}
        />
      ) : null}
    </div>
  );
}
