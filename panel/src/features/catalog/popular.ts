/**
 * The recipes a first visit is shown. Apart from the catalogue's model, which names its categories:
 * the Overview of an empty server reads this too, and must not load the catalogue's words for it.
 */

import type { RecipeSummary } from "../../api/queries/recipes";

/** The recipes a first visit is shown, in this order, when the catalogue has them and this release can deploy them. */
const POPULAR = ["uptime-kuma", "ghost", "wordpress", "n8n"] as const;

/** How many popular recipes are shown: a row, not a shelf. */
export const POPULAR_SHOWN = 4;

/**
 * Up to four recipes a person may install now: the popular ones first, in their order, then
 * whatever else is available. `hello` is the demo, offered on its own; a recipe this release
 * cannot deploy is never offered.
 */
export function popularRecipes(items: readonly RecipeSummary[] | undefined): RecipeSummary[] {
  if (items === undefined) return [];
  const available = items.filter((recipe) => recipe.available && recipe.name !== "hello");
  const chosen = POPULAR.flatMap((name) => available.filter((recipe) => recipe.name === name));
  const rest = available.filter((recipe) => !chosen.includes(recipe));
  return [...chosen, ...rest].slice(0, POPULAR_SHOWN);
}
