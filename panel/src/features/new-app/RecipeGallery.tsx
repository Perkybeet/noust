import { useState } from "react";

import type { RecipeSummary } from "../../api/queries/recipes";
import { CommandHint } from "../../components/page/CommandHint";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { CatalogFilterBar, CatalogResults, useRecipeCatalog } from "../catalog/RecipeCatalog";
import { RecipeLogo } from "../catalog/RecipeLogo";
import type { CatalogSearch } from "../catalog/model";
import { popularRecipes } from "../catalog/popular";

/** How many tiles the popular recipes make: the room held for them while the catalog is read. */
const POPULAR_PLACEHOLDERS = ["a", "b", "c", "d"] as const;

export interface RecipeGalleryProps {
  /** What the person typed in the step's one box: words to look for in the catalog. Empty shows the popular ones. */
  query: string;
  /** The step was opened on the recipes (`?start=recipe`): the whole catalog is shown, not the popular ones. */
  browse?: boolean;
  /** The recipe being read in full (`GET /api/recipes/{name}`) after it was chosen. */
  opening: string | null;
  /** Why reading the chosen recipe failed, verbatim. */
  failure: { title: string; error: unknown } | null;
  onChoose: (recipe: RecipeSummary) => void;
}

/**
 * The catalog inside the wizard, driven by the step's one box. With nothing typed: the popular recipes as
 * tiles with their logo and one button each, and a way to browse all of them with the catalog's own filters.
 * With words typed: what the catalog finds, as its cards, the ones this release can deploy first and the ones it
 * cannot folded away with the reason (items 93, 94, 98). The same components as the catalog page, so the two
 * cannot drift.
 */
export function RecipeGallery({ query, browse = false, opening, failure, onChoose }: RecipeGalleryProps) {
  const t = useT();
  const [browsing, setBrowsing] = useState(browse);
  const [filters, setFilters] = useState<CatalogSearch>({});
  const typed = query.trim() !== "";
  const search: CatalogSearch = typed ? { ...filters, q: query.trim() } : filters;
  const catalog = useRecipeCatalog(search);
  const notice = failure !== null ? <ErrorBlock live error={failure.error} title={failure.title} /> : undefined;

  if (typed || browsing) {
    return (
      <div className="flex flex-col gap-4">
        {browsing && !typed ? <CatalogFilterBar search={filters} onSearchChange={(next) => setFilters(next)} catalog={catalog} /> : null}
        <CatalogResults catalog={catalog} search={search} opening={opening} onClear={() => setFilters({})} onChoose={onChoose} {...(notice !== undefined ? { notice } : {})} />
        <CommandHint command="noust recipe list" label={t("common.words.fromTerminal")} />
      </div>
    );
  }

  if (catalog.everything.isPending) {
    // The tiles and the button are what the catalog says: until it has, the same room is held, so the ways
    // under them are not moved down when it arrives.
    return (
      <div className="flex flex-col gap-3">
        {notice}
        <Subsection title={t("newApp.recipes.startWith")}>
          <LoadingRegion label={t("newApp.recipes.loading")} className="grid gap-3 sm:grid-cols-2">
            {POPULAR_PLACEHOLDERS.map((place) => (
              <Skeleton key={place} className="h-18 rounded-card" />
            ))}
          </LoadingRegion>
        </Subsection>
        <div>
          <Skeleton className="h-8 w-40 rounded-control" />
        </div>
      </div>
    );
  }
  const popular = popularRecipes(catalog.everything.data?.items);
  return (
    <div className="flex flex-col gap-3">
      {notice}
      <Subsection title={t("newApp.recipes.startWith")}>
        <ul aria-label={t("newApp.recipes.popularLabel")} className="grid gap-3 sm:grid-cols-2">
          {popular.map((recipe) => (
            <Card as="li" key={recipe.name} padding="sm">
              <div className="flex min-w-0 items-center gap-3">
                <RecipeLogo logo={recipe.logo} title={recipe.title} />
                <span translate="no" className="min-w-0 flex-1 truncate text-13 font-medium text-fg">
                  {recipe.title}
                </span>
                <Button size="sm" loading={opening === recipe.name} disabled={opening !== null && opening !== recipe.name} aria-label={t("newApp.recipes.use", { title: recipe.title })} onClick={() => onChoose(recipe)}>
                  {t("catalog.card.use")}
                </Button>
              </div>
            </Card>
          ))}
        </ul>
      </Subsection>
      <div>
        <Button size="sm" variant="ghost" onClick={() => setBrowsing(true)}>
          {t("newApp.recipes.browseAll", { count: catalog.everything.data?.total ?? 0 })}
        </Button>
      </div>
    </div>
  );
}
