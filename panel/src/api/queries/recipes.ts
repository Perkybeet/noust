import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type RecipeList = ResponseOf<"/api/recipes", "get">;
export type RecipeSummary = RecipeList["items"][number];
export type Recipe = ResponseOf<"/api/recipes/{name}", "get">;

export const recipeKeys = {
  all: ["recipes"] as const,
  list: ["recipes", "list"] as const,
  detail: (name: string) => ["recipes", "detail", name] as const,
};

/** The recipes this release ships, the deployable ones first. They change only with Noust. */
export const recipesQuery = () =>
  queryOptions({
    queryKey: recipeKeys.list,
    queryFn: ({ signal }) => request("get", "/api/recipes", { signal }),
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
 * The notes a recipe's deploy left in its job's result (`result.notes`, filled in by the
 * server with the application's URL and domain), or null when the result carries none.
 */
export function recipeNotesOf(result: unknown): string[] | null {
  if (typeof result !== "object" || result === null) return null;
  const notes = (result as { notes?: unknown }).notes;
  if (!Array.isArray(notes)) return null;
  return notes.filter((note): note is string => typeof note === "string" && note.trim() !== "");
}
