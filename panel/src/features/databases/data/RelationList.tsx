import { Eye, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { RelationSummary } from "../../../api/queries/databases";
import { Input } from "../../../components/ui/Input";
import { ICONS } from "../../../components/ui/icons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { EmptyState } from "../../../components/ui/EmptyState";
import { useT } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatBytes, formatCount } from "../../../lib/format";

export interface RelationListProps {
  schemas: readonly string[];
  relations: readonly RelationSummary[] | undefined;
  selected: { schema: string; name: string } | null;
  onSelect: (relation: RelationSummary) => void;
  className?: string;
}

/**
 * A database's tables and views, by schema, with the planner's estimate of their rows and their
 * size on disk, and a box that narrows them by name. The one chosen is marked for sight and for
 * a screen reader (`aria-current`).
 */
export function RelationList({ schemas, relations, selected, onSelect, className }: RelationListProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const groups = useMemo(() => {
    const shown = (relations ?? []).filter((relation) => needle === "" || relation.name.toLowerCase().includes(needle));
    const names = schemas.length > 0 ? schemas : [...new Set(shown.map((relation) => relation.schema))];
    return names.map((schema) => ({ schema, items: shown.filter((relation) => relation.schema === schema) })).filter((group) => group.items.length > 0);
  }, [relations, schemas, needle]);
  const Search = ICONS.search;

  return (
    <nav aria-label={t("databases.data.tablesLabel")} className={cx("flex min-w-0 flex-col gap-2", className)}>
      <Input
        type="search"
        size="sm"
        icon={<Search />}
        value={query}
        onValueChange={(value: string) => setQuery(value)}
        placeholder={t("databases.data.findTable")}
        aria-label={t("databases.data.findTable")}
      />
      {relations === undefined ? (
        <div aria-hidden="true" className="flex flex-col gap-2 pt-1">
          {[0, 1, 2, 3, 4].map((index) => (
            <Skeleton key={index} className="h-7 w-full" />
          ))}
        </div>
      ) : groups.length === 0 ? (
        <EmptyState variant="inline" title={needle === "" ? t("databases.data.noTables") : t("databases.data.noTableMatches")} />
      ) : (
        <div className="flex max-h-editor min-w-0 flex-col gap-3 overflow-y-auto scroll-thin">
          {groups.map((group) => (
            <div key={group.schema} className="flex min-w-0 flex-col gap-0.5">
              {groups.length > 1 || schemas.length > 1 ? (
                <p className="px-2 pt-1 text-12 text-fg-faint" translate="no">
                  {group.schema}
                </p>
              ) : null}
              <ul className="flex min-w-0 flex-col gap-0.5">
                {group.items.map((relation) => {
                  const current = selected?.schema === relation.schema && selected.name === relation.name;
                  const Icon = relation.kind === "table" ? Table2 : Eye;
                  return (
                    <li key={`${relation.schema}.${relation.name}`}>
                      <button
                        type="button"
                        onClick={() => onSelect(relation)}
                        aria-current={current ? "true" : undefined}
                        className={cx(
                          "flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-control px-2 py-1.5 text-left text-13",
                          current ? "bg-accent-soft text-fg" : "text-fg-muted hover:bg-surface-hover hover:text-fg",
                        )}
                      >
                        <Icon aria-hidden="true" className={cx("size-icon-sm shrink-0", current ? "text-accent-fg" : "text-fg-faint")} />
                        <span translate="no" className="min-w-0 flex-1 truncate font-medium">
                          {relation.name}
                        </span>
                        <span className="shrink-0 text-12 text-fg-faint tabular-nums">
                          {relation.rows_estimate != null
                            ? t("databases.data.rowsShort", { rows: formatCount(relation.rows_estimate, t.locale) })
                            : relation.size_bytes != null
                              ? formatBytes(relation.size_bytes, t.locale)
                              : relation.kind === "table"
                                ? ""
                                : t("databases.data.view")}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </nav>
  );
}
