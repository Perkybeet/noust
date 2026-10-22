import { useNavigate } from "@tanstack/react-router";

import { Section, Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import { CatalogFrame } from "./CatalogFrame";
import { RecipeCard } from "./RecipeCard";
import { CatalogFilterBar, CatalogResults, useRecipeCatalog } from "./RecipeCatalog";
import { isNarrowed } from "./model";
import type { CatalogSearch } from "./model";
import { POPULAR_SHOWN, popularRecipes } from "./popular";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

export interface CatalogPageProps {
  search: CatalogSearch;
  /** Sets the filters in the URL: `replace` for typing, so the history does not gain a step per key. */
  onSearchChange: (search: CatalogSearch, options?: { replace?: boolean }) => void;
}

/**
 * The catalogue (T1): a tile for each recipe the server ships, searchable and filtered by
 * category, tag and what it runs on, kept in the URL so a filtered view can be shared. Before any
 * filter is set, the ones most people start with come first, in a row of their own. Using a
 * recipe opens the wizard on its install form, where the address and the variables it needs are
 * asked and the job is followed to its end: the catalogue never deploys on its own.
 */
export function CatalogPage({ search, onSearchChange }: CatalogPageProps) {
  const t = useT();
  const navigate = useNavigate();
  const catalog = useRecipeCatalog(search);
  // A row of the ones most people start with, above the rest: not for a filtered view, and not for
  // a catalogue so small that the row would be all of it.
  const everything = catalog.everything.data?.items;
  const popular = isNarrowed(search) || everything === undefined || everything.length <= POPULAR_SHOWN * 2 ? [] : popularRecipes(everything);
  const choose = (name: string): void => void navigate({ to: "/apps/new", search: { start: "recipe", recipe: name } });
  const results = (
    <CatalogResults
      catalog={catalog}
      search={search}
      paged
      detail
      level={popular.length > 0 ? 3 : 2}
      onClear={() => onSearchChange({})}
      onChoose={(recipe) => choose(recipe.name)}
    />
  );
  return (
    <CatalogFrame command="noust recipe list" filters={<CatalogFilterBar search={search} onSearchChange={onSearchChange} catalog={catalog} />}>
      {popular.length > 0 ? (
        <Sections>
          <Section title={t("catalog.popular.title")} icon={OBJECT_ICONS.recipe}>
            <ul aria-label={t("catalog.popular.title")} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {popular.map((recipe) => (
                <RecipeCard key={recipe.name} recipe={recipe} detail level={3} onChoose={() => choose(recipe.name)} />
              ))}
            </ul>
          </Section>
          <Section title={t("catalog.all.title")} icon={OBJECT_ICONS.recipe}>{results}</Section>
        </Sections>
      ) : (
        results
      )}
    </CatalogFrame>
  );
}
