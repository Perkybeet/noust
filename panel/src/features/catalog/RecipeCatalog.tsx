import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { recipeCategoriesQuery, recipesQuery } from "../../api/queries/recipes";
import type { RecipeSummary } from "../../api/queries/recipes";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Combobox } from "../../components/ui/Combobox";
import { EmptyState } from "../../components/ui/EmptyState";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { TablePagination, pageWindow } from "../../components/ui/TablePagination";
import { usePageSize } from "../../components/ui/pageSize";
import { useT } from "../../i18n";
import { formatCount } from "../../lib/format";
import { CATEGORIES, KINDS, filtersOf, isNarrowed, pageOf, tagCounts } from "./model";
import type { CatalogSearch } from "./model";
import { RecipeCard, categoryWords } from "./RecipeCard";

const ALL = "all";

/** The name the catalogue's page size is remembered under (`noust.pageSize.recipes`). */
export const CATALOG_LIST = "recipes";

/**
 * What the catalogue's filter bar and its grid share: the whole catalogue (for the tags it
 * offers and the total it counts against), what the filters keep of it, and the categories
 * that hold a recipe. The filtering is the server's (`GET /api/recipes?q=&category=&tag=&kind=`):
 * one search, the same for the CLI and for here.
 */
export function useRecipeCatalog(search: CatalogSearch) {
  const everything = useQuery(recipesQuery());
  // The previous answer stays on screen while the next one is read: typing never blanks the grid.
  const found = useQuery({ ...recipesQuery(filtersOf(search)), placeholderData: keepPreviousData });
  const categories = useQuery(recipeCategoriesQuery());
  const tags = useMemo(() => tagCounts(everything.data?.items ?? []), [everything.data]);
  return { everything, found, categories, tags };
}

export type RecipeCatalogData = ReturnType<typeof useRecipeCatalog>;

type SearchPatch = { [K in keyof CatalogSearch]?: CatalogSearch[K] | undefined };

/** A change to the filters, as the URL (or the wizard's own state) keeps them: unset keys leave. */
export function nextSearch(search: CatalogSearch, patch: SearchPatch): CatalogSearch {
  const merged = { ...search, ...patch };
  const clean: CatalogSearch = {};
  if (merged.q) clean.q = merged.q;
  if (merged.category) clean.category = merged.category;
  if (merged.tag) clean.tag = merged.tag;
  if (merged.kind) clean.kind = merged.kind;
  return clean;
}

export interface CatalogFilterBarProps {
  search: CatalogSearch;
  /** `replace` for typing, so the history does not gain an entry per keystroke. */
  onSearchChange: (search: CatalogSearch, options?: { replace?: boolean }) => void;
  catalog: RecipeCatalogData;
}

/**
 * Search and the three filters of the catalogue: a category (the closed list, with how many
 * recipes each holds), a tag (found by typing: there are dozens) and whether it runs in Docker
 * or natively. The count says how many of the whole catalogue are shown.
 */
export function CatalogFilterBar({ search, onSearchChange, catalog }: CatalogFilterBarProps) {
  const t = useT();
  const set = (patch: SearchPatch, replace = false): void => onSearchChange(nextSearch(search, patch), { replace });
  const total = catalog.everything.data?.total;
  const shown = catalog.found.data?.total;
  const narrowed = isNarrowed(search);

  const counted = new Map((catalog.categories.data?.items ?? []).map((entry) => [entry.category, entry.count]));
  // The closed list's order first; a category only a newer server knows follows, as it spells it.
  const known = CATEGORIES.filter((category) => counted.has(category));
  const other = [...counted.keys()].filter((category) => !(CATEGORIES as readonly string[]).includes(category));
  const categoryOptions = [
    { value: ALL, label: t("catalog.filters.everyCategory") },
    ...[...known, ...other].map((category) => ({
      value: category,
      label: t("catalog.filters.categoryOption", { name: categoryWords(t, category), count: counted.get(category) ?? 0 }),
    })),
  ];
  // A category or a tag in the URL that the catalogue does not hold is still shown as chosen.
  if (search.category !== undefined && !counted.has(search.category)) {
    categoryOptions.push({ value: search.category, label: categoryWords(t, search.category) });
  }
  const tagItems = [
    { value: ALL, label: t("catalog.filters.everyTag") },
    ...catalog.tags.map(({ tag, count }) => ({ value: tag, label: t("catalog.filters.tagOption", { tag, count }) })),
  ];
  if (search.tag !== undefined && !catalog.tags.some(({ tag }) => tag === search.tag)) {
    tagItems.push({ value: search.tag, label: search.tag });
  }
  const kindOptions = [
    { value: ALL, label: t("catalog.filters.everyKind") },
    ...KINDS.map((kind) => ({ value: kind, label: t(kind === "compose" ? "catalog.filters.compose" : "catalog.filters.native") })),
  ];

  return (
    <FilterBar
      label={t("catalog.filters.label")}
      search={{
        value: search.q ?? "",
        onChange: (value) => set({ q: value }, true),
        label: t("catalog.filters.search"),
        placeholder: t("catalog.filters.searchPlaceholder"),
      }}
      filters={
        <>
          <Select
            aria-label={t("catalog.filters.category")}
            value={search.category ?? ALL}
            onValueChange={(value) => set({ category: value === ALL ? undefined : value })}
            options={categoryOptions}
            className="min-w-44"
          />
          <Combobox
            aria-label={t("catalog.filters.tag")}
            items={tagItems}
            value={search.tag ?? ALL}
            onValueChange={(value) => set({ tag: value === ALL ? undefined : value })}
            emptyMessage={t("catalog.filters.noTag")}
            className="w-44"
          />
          <Select
            aria-label={t("catalog.filters.kind")}
            value={search.kind ?? ALL}
            onValueChange={(value) => set({ kind: KINDS.find((kind) => kind === value) })}
            options={kindOptions}
            className="min-w-40"
          />
        </>
      }
      {...(shown !== undefined && total !== undefined
        ? { count: narrowed ? t("catalog.filters.countFiltered", { shown: formatCount(shown, t.locale), total: formatCount(total, t.locale) }) : t("catalog.filters.count", { count: total }) }
        : {})}
      {...(narrowed
        ? {
            actions: (
              <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                {t("catalog.filters.clear")}
              </Button>
            ),
          }
        : {})}
    />
  );
}

/** The grid of cards while the first read is out: the shape of what arrives, so nothing moves. */
function CatalogSkeleton() {
  const t = useT();
  return (
    <div aria-busy="true" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      <span className="sr-only">{t("newApp.recipes.loading")}</span>
      {["a", "b", "c", "d", "e", "f"].map((key) => (
        <Skeleton key={key} className="h-52" />
      ))}
    </div>
  );
}

export interface CatalogResultsProps {
  catalog: RecipeCatalogData;
  search: CatalogSearch;
  onClear: () => void;
  /** What "Use" does for a recipe, or nothing: a card without the button. */
  onChoose?: (recipe: RecipeSummary) => void;
  /** The recipe being read in full after it was chosen, to show its card working. */
  opening?: string | null;
  /** Cards that link to the recipe's page: where the catalogue is browsed. */
  detail?: boolean;
  /** Pages at the operator's size (20, 50, 100 or all) instead of listing every recipe. */
  paged?: boolean;
  /** The heading level of each recipe's name: 2 under a page's title, 3 (the default) inside a step. */
  level?: 2 | 3;
  /** What stands above the grid: a failure to read the recipe that was chosen. */
  notice?: ReactNode;
}

/**
 * What the filters keep of the catalogue, as cards, the ones this release can deploy first. A
 * filter that keeps nothing says so and offers to clear itself; the empty catalogue says that.
 * Paging is the operator's (design 6.8): shown only when the page is asked to page, and only
 * once there are more recipes than the smallest page.
 */
export function CatalogResults({ catalog, search, onClear, onChoose, opening = null, detail = false, paged = false, level = 3, notice }: CatalogResultsProps) {
  const t = useT();
  const [pageSize, setPageSize] = usePageSize(CATALOG_LIST);
  // The page is forgotten with the filters it was a page of: a new search starts at the first.
  const searchKey = JSON.stringify(search);
  const [at, setAt] = useState<{ key: string; page: number }>({ key: searchKey, page: 0 });
  const page = at.key === searchKey ? at.page : 0;

  const { found } = catalog;
  if (found.data === undefined) {
    return found.isError ? (
      <ErrorBlock error={found.error} title={t("newApp.recipes.loadFailed")} onRetry={() => void found.refetch()} retrying={found.isFetching} />
    ) : (
      <CatalogSkeleton />
    );
  }
  const items = found.data.items;
  if (items.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        {notice}
        {isNarrowed(search) ? (
          <EmptyState
            variant="inline"
            title={t("catalog.empty.noMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={onClear}>
                {t("catalog.filters.clear")}
              </Button>
            }
          />
        ) : (
          <EmptyState variant="inline" title={t("newApp.recipes.empty")} />
        )}
      </div>
    );
  }

  const slice = pageWindow(items.length, pageSize, page);
  const shown = paged ? pageOf(items, slice.from, slice.to) : items;
  return (
    <div className="flex flex-col gap-4">
      {notice}
      <ul aria-label={t("newApp.recipes.label")} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((recipe) => (
          <RecipeCard
            key={recipe.name}
            recipe={recipe}
            opening={opening === recipe.name}
            disabled={opening !== null}
            detail={detail}
            level={level}
            {...(onChoose !== undefined ? { onChoose: () => onChoose(recipe) } : {})}
          />
        ))}
      </ul>
      {paged && items.length > 20 ? (
        <TablePagination
          total={items.length}
          page={slice.page}
          pageSize={pageSize}
          onPageChange={(next) => setAt({ key: searchKey, page: next })}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setAt({ key: searchKey, page: 0 });
          }}
        />
      ) : null}
    </div>
  );
}
