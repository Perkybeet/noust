import { useState } from "react";

import type { RecipeSummary } from "../../api/queries/recipes";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { useT } from "../../i18n";
import { CatalogFilterBar, CatalogResults, useRecipeCatalog } from "../catalog/RecipeCatalog";
import type { CatalogSearch } from "../catalog/model";

export interface RecipeGalleryProps {
  /** The recipe being read (`GET /api/recipes/{name}`) after it was chosen. */
  opening: string | null;
  /** Why reading the chosen recipe failed, verbatim. */
  failure: { title: string; error: unknown } | null;
  onChoose: (recipe: RecipeSummary) => void;
}

/**
 * The recipes this release ships, as the catalog's cards, searchable and filtered the way the
 * catalog is (the same components, so the two cannot drift): what each is, what it needs from
 * this server, its version and its website. The ones it cannot deploy yet are shown too,
 * marked, with the reason the server gives. Here the filters are the step's own, never the
 * address's: the wizard's URL says where the operator is in it, not what they searched for.
 */
export function RecipeGallery({ opening, failure, onChoose }: RecipeGalleryProps) {
  const t = useT();
  const [search, setSearch] = useState<CatalogSearch>({});
  const catalog = useRecipeCatalog(search);
  return (
    <div className="flex flex-col gap-4">
      <CatalogFilterBar search={search} onSearchChange={(next) => setSearch(next)} catalog={catalog} />
      <CatalogResults
        catalog={catalog}
        search={search}
        opening={opening}
        onClear={() => setSearch({})}
        onChoose={onChoose}
        notice={failure !== null ? <ErrorBlock live error={failure.error} title={failure.title} /> : undefined}
      />
      <CommandHint command="noust recipe list" label={t("newApp.source.terminal")} />
    </div>
  );
}
