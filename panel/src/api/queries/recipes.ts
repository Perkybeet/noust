import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, QueryOf, ResponseOf } from "../client";
import { activeNode, nodeApiPath } from "../nodeScope";

export type RecipeList = ResponseOf<"/api/recipes", "get">;
export type RecipeSummary = RecipeList["items"][number];
export type Recipe = ResponseOf<"/api/recipes/{name}", "get">;
/** One variable of a recipe, with where its value comes from and whether install needs it. */
export type RecipeVariableInfo = Recipe["variables"][number];
export type RecipeCategories = ResponseOf<"/api/recipes/categories", "get">;
export type InstalledRecipes = ResponseOf<"/api/recipes/installed", "get">;
export type InstalledRecipe = InstalledRecipes["items"][number];
export type RecipeChange = InstalledRecipe["changes"][number];
export type RecipeUpdateBody = BodyOf<"/api/recipes/installed/{domain}/update", "post">;
/** What a recipe needs from the server, and whether it has it (`GET /api/recipes/{name}/requirements`). */
export type RecipeRequirements = ResponseOf<"/api/recipes/{name}/requirements", "get">;
export type RecipeRequirement = RecipeRequirements["items"][number];
/** A file in the server's own recipes that was left out, and why. */
export type RecipeProblem = NonNullable<RecipeList["problems"]>[number];
/** The signed package of recipes in place, and the keys that may sign one. */
export type CatalogState = ResponseOf<"/api/recipes/catalog", "get">;
/** What a recipe of the server's own looks like to the catalogue, checked or added. */
export type PrivateRecipeResult = ResponseOf<"/api/recipes/private/check", "post">;
type RecipeQuery = QueryOf<"/api/recipes", "get">;

/** What narrows the catalogue; every one given must hold (the server's own search). */
export interface RecipeFilters {
  /** Words to look for in the name, title, slogan, description, category and tags. */
  q?: string | undefined;
  /** One category of the closed list. */
  category?: string | undefined;
  /** One tag, exactly. */
  tag?: string | undefined;
  /** `compose` runs in Docker; `native` is built and run by one of Noust's deployers. */
  kind?: "native" | "compose" | undefined;
}

export const recipeKeys = {
  all: ["recipes"] as const,
  list: ["recipes", "list"] as const,
  filtered: (filters: object) => ["recipes", "list", filters] as const,
  detail: (name: string) => ["recipes", "detail", name] as const,
  categories: ["recipes", "categories"] as const,
  installed: ["recipes", "installed"] as const,
  requirements: (name: string) => ["recipes", "requirements", name] as const,
  catalogState: ["recipes", "catalog"] as const,
};

/** The filters that are set, as the query string takes them: nothing empty is sent. */
export function cleanRecipeFilters(filters: RecipeFilters): NonNullable<RecipeQuery> {
  const clean: NonNullable<RecipeQuery> = {};
  const q = filters.q?.trim();
  if (q !== undefined && q !== "") clean.q = q;
  if (filters.category !== undefined && filters.category !== "") clean.category = filters.category;
  if (filters.tag !== undefined && filters.tag !== "") clean.tag = filters.tag;
  if (filters.kind !== undefined) clean.kind = filters.kind;
  return clean;
}

/**
 * The recipes this release ships, the deployable ones first, narrowed by the server's own
 * search when `filters` say so. They change only with Noust, so a read is good for minutes.
 */
export const recipesQuery = (filters: RecipeFilters = {}) => {
  const clean = cleanRecipeFilters(filters);
  const narrowed = Object.keys(clean).length > 0;
  return queryOptions({
    queryKey: narrowed ? recipeKeys.filtered(clean) : recipeKeys.list,
    queryFn: ({ signal }) => request("get", "/api/recipes", { ...(narrowed ? { query: clean } : {}), signal }),
    staleTime: 10 * 60_000,
  });
};

/** The categories that hold at least one recipe, with how many. */
export const recipeCategoriesQuery = () =>
  queryOptions({
    queryKey: recipeKeys.categories,
    queryFn: ({ signal }) => request("get", "/api/recipes/categories", { signal }),
    staleTime: 10 * 60_000,
  });

/** One recipe in full: its source, variables, database, persistent paths and notes. */
export const recipeQuery = (name: string) =>
  queryOptions({
    queryKey: recipeKeys.detail(name),
    queryFn: ({ signal }) => request("get", "/api/recipes/{name}", { params: { name }, signal }),
    staleTime: 10 * 60_000,
  });

/**
 * What a recipe needs from this server (tools, a language runtime, database engines) and
 * whether it has each. It changes when the operator installs one, so it is read again on
 * every visit of the install form.
 */
export const recipeRequirementsQuery = (name: string) =>
  queryOptions({
    queryKey: recipeKeys.requirements(name),
    queryFn: ({ signal }) => request("get", "/api/recipes/{name}/requirements", { params: { name }, signal }),
    staleTime: 0,
  });

/** The signed package of recipes in place, if any, and the names of the keys it may be signed by. */
export const catalogStateQuery = () =>
  queryOptions({
    queryKey: recipeKeys.catalogState,
    queryFn: ({ signal }) => request("get", "/api/recipes/catalog", { signal }),
    staleTime: 60_000,
  });

/**
 * What was installed from a recipe, at which version, and whether the catalogue is ahead. It
 * moves with every update, so it is read again whenever the page is opened.
 */
export const installedRecipesQuery = () =>
  queryOptions({
    queryKey: recipeKeys.installed,
    queryFn: ({ signal }) => request("get", "/api/recipes/installed", { signal }),
    staleTime: 30_000,
  });

/**
 * Where a recipe's logo is served from, for an `<img>`: on a central with a node selected the
 * node's own copy, through the proxy, like every other call. The logo is an SVG held to a
 * closed list of elements, so it needs nothing the Content Security Policy forbids.
 */
export function recipeLogoSrc(logo: string): string {
  return nodeApiPath(activeNode(), logo);
}

/**
 * The notes a recipe's deploy left in its job's result (`result.notes`, filled in by the
 * server with the application's URL and domain), or null when the result carries none.
 */
export function recipeNotesOf(result: unknown): string[] | null {
  if (typeof result !== "object" || result === null) return null;
  const notes = (result as { notes?: unknown }).notes;
  if (!Array.isArray(notes)) return null;
  return notes.filter((note): note is string => typeof note === "string" && note.trim() !== "");
}
