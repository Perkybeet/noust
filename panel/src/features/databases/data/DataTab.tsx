import { useQuery } from "@tanstack/react-query";
import { Table2 } from "lucide-react";

import { catalogQuery, databaseOverviewQuery, enginesQuery } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { LG_UP, useMediaQuery } from "../../../components/ui/useMediaQuery";
import { useT } from "../../../i18n";
import { can } from "../engines";
import { KeysView } from "./KeysView";
import { RelationList } from "./RelationList";
import type { DataSearch, DataSearchPatch } from "./search";
import { TableView } from "./TableView";

export interface DataTabProps {
  engine: string;
  name: string;
  search: DataSearch;
  onSearchChange: (search: DataSearch, options?: { replace?: boolean }) => void;
}

/** The first table to open: the biggest by its estimate, which is usually the one looked for. */
function defaultRelation<T extends { schema: string; name: string; kind: string; rows_estimate?: number | null }>(relations: readonly T[]): T | undefined {
  const tables = relations.filter((relation) => relation.kind === "table");
  return [...(tables.length > 0 ? tables : relations)].sort((a, b) => (b.rows_estimate ?? 0) - (a.rows_estimate ?? 0))[0];
}

/**
 * What a database holds, read as its read-only account: its tables and views beside the one
 * open (a list on a wide screen, a menu on a phone), or, for Redis, its keys. The table, its
 * view, order and filters are the URL's.
 */
export function DataTab({ engine, name, search, onSearchChange }: DataTabProps) {
  const t = useT();
  const wide = useMediaQuery(LG_UP);
  const engines = useQuery(enginesQuery());
  const overview = useQuery(databaseOverviewQuery(engine, name));
  const capabilities = overview.data?.capabilities ?? engines.data?.engines.find((item) => item.name === engine)?.capabilities;
  const keys = capabilities !== undefined && can({ capabilities }, "keys") && !can({ capabilities }, "tables");
  const catalog = useQuery({ ...catalogQuery(engine, name), enabled: capabilities !== undefined && !keys });

  const patch = (next: DataSearchPatch, replace = false): void => {
    const merged: DataSearchPatch = { ...search, ...next };
    const clean = Object.fromEntries(Object.entries(merged).filter(([, value]) => value !== undefined)) as DataSearch;
    onSearchChange(clean, { replace });
  };

  if (keys) return <KeysView engine={engine} name={name} search={search} onSearchChange={(next) => patch(next, true)} />;

  const relations = catalog.data?.relations;
  const fallback = relations !== undefined ? defaultRelation(relations) : undefined;
  const selected =
    search.schema !== undefined && search.table !== undefined ? { schema: search.schema, name: search.table } : fallback !== undefined ? { schema: fallback.schema, name: fallback.name } : null;

  if (catalog.isError && relations === undefined) {
    return <ErrorBlock error={catalog.error} title={t("databases.data.catalogFailed")} onRetry={() => void catalog.refetch()} retrying={catalog.isRefetching} />;
  }

  if (relations?.length === 0) {
    return (
      <EmptyState
        variant="firstUse"
        icon={<Table2 />}
        title={t("databases.data.emptyTitle")}
        description={t("databases.data.emptyDescription")}
        command={`noust db tables ${name} -e ${engine}`}
      />
    );
  }

  const choose = (schema: string, table: string): void => {
    // Another table: its own order and filters, not the last one's.
    onSearchChange({ schema, table });
  };

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-start lg:gap-6">
        {wide ? (
          <RelationList
            className="w-64 shrink-0"
            schemas={catalog.data?.schemas.map((schema) => schema.name) ?? []}
            relations={relations}
            selected={selected}
            onSelect={(relation) => choose(relation.schema, relation.name)}
          />
        ) : (
          <Select
            aria-label={t("databases.data.tablesLabel")}
            value={selected !== null ? `${selected.schema}.${selected.name}` : null}
            placeholder={t("databases.data.chooseTable")}
            mono
            onValueChange={(value) => {
              const found = relations?.find((relation) => `${relation.schema}.${relation.name}` === value);
              if (found !== undefined) choose(found.schema, found.name);
            }}
            options={(relations ?? []).map((relation) => ({ value: `${relation.schema}.${relation.name}`, label: `${relation.schema}.${relation.name}` }))}
          />
        )}
        <div className="min-w-0 flex-1">
          {selected !== null ? (
            <TableView
              key={`${selected.schema}.${selected.name}`}
              engine={engine}
              name={name}
              schema={selected.schema}
              relation={selected.name}
              search={search}
              onSearchChange={(next) => patch({ schema: selected.schema, table: selected.name, ...next })}
            />
          ) : relations === undefined ? (
            // The table that opens first is known only once the catalog is: its room is held.
            <LoadingRegion label={t("databases.data.loadingTables")}>
              <Skeleton className="h-64 w-full rounded-card" />
            </LoadingRegion>
          ) : null}
        </div>
      </div>
      <CommandHint command={`noust db rows ${name} ${selected?.name ?? "<table>"} -e ${engine}`} label={t("databases.common.fromTerminal")} />
    </div>
  );
}
