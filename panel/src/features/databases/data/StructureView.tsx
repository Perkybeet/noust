import { KeyRound } from "lucide-react";

import type { RelationDetail } from "../../../api/queries/databases";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { DataTable } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";

const CONSTRAINT_WORDS: Readonly<Record<string, PlainKey>> = {
  primary_key: "databases.structure.constraint.primaryKey",
  foreign_key: "databases.structure.constraint.foreignKey",
  unique: "databases.structure.constraint.unique",
  check: "databases.structure.constraint.check",
  exclusion: "databases.structure.constraint.exclusion",
  trigger: "databases.structure.constraint.trigger",
  other: "databases.structure.constraint.other",
};

/**
 * A table's structure as the engine describes it: its columns (type, whether they take NULL,
 * default, place in the primary key), its indexes and its constraints, their definitions
 * verbatim.
 */
export function StructureView({ detail }: { detail: RelationDetail | undefined }) {
  const t = useT();
  if (detail === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-2">
        <span className="sr-only">{t("databases.structure.loading")}</span>
        <Skeleton className="h-56 w-full rounded-card" />
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-8">
      <Section title={t("databases.structure.columns")}>
        <DataTable
          caption={t("databases.structure.columnsOf", { name: `${detail.schema}.${detail.name}` })}
          density="compact"
          rows={detail.columns}
          getRowId={(column) => column.name}
          columns={[
            {
              id: "name",
              header: t("databases.structure.name"),
              cell: (column) => (
                <span className="flex items-center gap-1.5">
                  {column.primary_key != null ? <KeyRound aria-label={t("databases.grid.primaryKey")} className="size-icon-xs text-fg-muted" /> : null}
                  <Mono>{column.name}</Mono>
                </span>
              ),
            },
            { id: "type", header: t("databases.structure.type"), cell: (column) => <Mono tone="muted">{column.type}</Mono> },
            {
              id: "nullable",
              header: t("databases.structure.nullable"),
              cell: (column) => (column.nullable ? t("databases.structure.yes") : t("databases.structure.no")),
            },
            {
              id: "default",
              header: t("databases.structure.default"),
              cell: (column) =>
                column.default != null ? (
                  <Mono tone="muted" truncate>
                    {column.default}
                  </Mono>
                ) : column.generated ? (
                  <span className="text-fg-muted">{t("databases.structure.generated")}</span>
                ) : (
                  <EmptyCell reason={t("databases.structure.noDefault")} />
                ),
            },
          ]}
        />
      </Section>
      <Section title={t("databases.structure.indexes")}>
        {(detail.indexes ?? []).length === 0 ? (
          <EmptyState variant="inline" title={t("databases.structure.noIndexes")} />
        ) : (
          <DataTable
            caption={t("databases.structure.indexesOf", { name: `${detail.schema}.${detail.name}` })}
            density="compact"
            rows={detail.indexes ?? []}
            getRowId={(index) => index.name}
            columns={[
              {
                id: "name",
                header: t("databases.structure.name"),
                cell: (index) => (
                  <span className="flex items-center gap-2">
                    <Mono>{index.name}</Mono>
                    {index.primary ? <Badge>{t("databases.structure.primary")}</Badge> : index.unique ? <Badge>{t("databases.structure.unique")}</Badge> : null}
                  </span>
                ),
              },
              { id: "columns", header: t("databases.structure.columns"), cell: (index) => <Mono tone="muted">{(index.columns ?? []).join(", ")}</Mono> },
              {
                id: "definition",
                header: t("databases.structure.definition"),
                hideBelow: "md",
                cell: (index) =>
                  index.definition ? (
                    <Mono tone="muted" truncate>
                      {index.definition}
                    </Mono>
                  ) : (
                    <EmptyCell reason={t("databases.structure.noDefinition")} />
                  ),
              },
            ]}
          />
        )}
      </Section>
      <Section title={t("databases.structure.constraints")}>
        {(detail.constraints ?? []).length === 0 ? (
          <EmptyState variant="inline" title={t("databases.structure.noConstraints")} />
        ) : (
          <DataTable
            caption={t("databases.structure.constraintsOf", { name: `${detail.schema}.${detail.name}` })}
            density="compact"
            rows={detail.constraints ?? []}
            getRowId={(constraint) => constraint.name}
            columns={[
              { id: "name", header: t("databases.structure.name"), cell: (constraint) => <Mono>{constraint.name}</Mono> },
              { id: "type", header: t("databases.structure.kind"), cell: (constraint) => t(CONSTRAINT_WORDS[constraint.type] ?? "databases.structure.constraint.other") },
              {
                id: "definition",
                header: t("databases.structure.definition"),
                cell: (constraint) =>
                  constraint.definition ? (
                    <Mono tone="muted" truncate>
                      {constraint.definition}
                    </Mono>
                  ) : (
                    <EmptyCell reason={t("databases.structure.noDefinition")} />
                  ),
              },
            ]}
          />
        )}
      </Section>
    </div>
  );
}
