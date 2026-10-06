import { useNavigate } from "@tanstack/react-router";

import { CatalogFrame } from "./CatalogFrame";
import { CatalogFilterBar, CatalogResults, useRecipeCatalog } from "./RecipeCatalog";
import type { CatalogSearch } from "./model";

export interface CatalogPageProps {
  search: CatalogSearch;
  /** Sets the filters in the URL: `replace` for typing, so the history does not gain a step per key. */
  onSearchChange: (search: CatalogSearch, options?: { replace?: boolean }) => void;
}

/**
 * The catalogue (T1): a card for each recipe the server ships, searchable and filtered by
 * category, tag and what it runs on, kept in the URL so a filtered view can be shared. Using a
 * recipe opens the wizard on its install form, where the address and the variables it needs are
 * asked and the job is followed to its end: the catalogue never deploys on its own.
 */
export function CatalogPage({ search, onSearchChange }: CatalogPageProps) {
  const navigate = useNavigate();
  const catalog = useRecipeCatalog(search);
  return (
    <CatalogFrame command="noust recipe list" filters={<CatalogFilterBar search={search} onSearchChange={onSearchChange} catalog={catalog} />}>
      <CatalogResults
        catalog={catalog}
        search={search}
        paged
        detail
        level={2}
        onClear={() => onSearchChange({})}
        onChoose={(recipe) => void navigate({ to: "/apps/new", search: { start: "recipe", recipe: recipe.name } })}
      />
    </CatalogFrame>
  );
}
