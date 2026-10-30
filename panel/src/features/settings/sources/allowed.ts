/**
 * Where code may come from on this server (`security.allowed_sources`). A server with no list allows every
 * origin and is not asked about. One that names them refuses the folder a catalog recipe is prepared in unless the
 * word `recipes` (or `local`) is on the list, so Noust would block itself: the console offers to allow the recipes.
 */

/** The word that allows the recipes of the catalog as an origin of code. */
export const RECIPES = "recipes";

/** The entries of the list, as the configuration holds them; none when it holds none or something else. */
export function readAllowedSources(config: unknown): string[] {
  if (typeof config !== "object" || config === null) return [];
  const security = (config as Record<string, unknown>)["security"];
  if (typeof security !== "object" || security === null) return [];
  const list = (security as Record<string, unknown>)["allowed_sources"];
  return Array.isArray(list) ? list.filter((entry): entry is string => typeof entry === "string") : [];
}

/** Whether the recipes of the catalog may be deployed: with no list everything is allowed, and with one the word is on it. */
export function recipesAllowed(entries: readonly string[]): boolean {
  if (entries.length === 0) return true;
  return entries.some((entry) => ["recipes", "local"].includes(entry.trim().toLowerCase()));
}

/** The list with the recipes allowed: the entries it has, then the word, once. */
export function withRecipes(entries: readonly string[]): string[] {
  return recipesAllowed(entries) && entries.some((entry) => entry.trim().toLowerCase() === RECIPES) ? [...entries] : [...entries, RECIPES];
}
