/**
 * The catalogue's logic, apart from any page: which filters the URL carries, the closed list of
 * categories, the tags the filter offers, and what a recipe says about itself in a line. Pure,
 * so each rule is tested without drawing a card.
 */

import type { InstalledRecipe, RecipeFilters, RecipeSummary } from "../../api/queries/recipes";
import type { PlainKey } from "../../i18n";

/**
 * The categories a recipe may be in, in the order the filter lists them. The list is closed on
 * purpose (docs/recipes.md): Coolify's free text ended up with 38 spellings of it. A category
 * the server sends that is not here (a newer server) is shown as it is, never dropped.
 */
export const CATEGORIES = [
  "ai",
  "analytics",
  "automation",
  "backend",
  "business",
  "cms",
  "communication",
  "data",
  "development",
  "files",
  "media",
  "monitoring",
  "productivity",
  "search",
  "security",
  "smart-home",
  "utilities",
  "other",
] as const;

export type Category = (typeof CATEGORIES)[number];

/** The catalog key of each category's name: one literal per case so tsc checks them. */
export const CATEGORY_LABELS: Readonly<Record<Category, PlainKey>> = {
  ai: "catalog.categories.ai",
  analytics: "catalog.categories.analytics",
  automation: "catalog.categories.automation",
  backend: "catalog.categories.backend",
  business: "catalog.categories.business",
  cms: "catalog.categories.cms",
  communication: "catalog.categories.communication",
  data: "catalog.categories.data",
  development: "catalog.categories.development",
  files: "catalog.categories.files",
  media: "catalog.categories.media",
  monitoring: "catalog.categories.monitoring",
  productivity: "catalog.categories.productivity",
  search: "catalog.categories.search",
  security: "catalog.categories.security",
  "smart-home": "catalog.categories.smartHome",
  utilities: "catalog.categories.utilities",
  other: "catalog.categories.other",
};

/** Whether a category is one of the closed list. */
export function isCategory(value: string): value is Category {
  return (CATEGORIES as readonly string[]).includes(value);
}

/** The key naming a category, or null for one this console does not know (shown as it is). */
export function categoryLabelKey(category: string): PlainKey | null {
  return isCategory(category) ? CATEGORY_LABELS[category] : null;
}

/** What a recipe runs as: in Docker (a Compose stack Noust writes) or built and run natively. */
export const KINDS = ["compose", "native"] as const;

export type Kind = (typeof KINDS)[number];

/** The filters of the catalogue page, which live in the URL (`/catalog?q=wiki&category=cms`). */
export interface CatalogSearch {
  /** Free text, matched by the server against the name, title, slogan, description, category and tags. */
  q?: string;
  category?: string;
  tag?: string;
  kind?: Kind;
}

function text(value: unknown, max: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, max);
}

/** A tag as the server holds it: lower case, letters, digits and hyphens. */
const TAG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

/**
 * Reads the search params, dropping anything malformed instead of failing: a hand-edited or
 * stale link still opens the catalogue, just less filtered.
 */
export function validateCatalogSearch(search: Record<string, unknown>): CatalogSearch {
  const q = text(search["q"], 100);
  const category = text(search["category"], 40);
  const tag = text(search["tag"], 40);
  const kind = text(search["kind"], 10);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(category !== undefined && /^[a-z][a-z-]*$/.test(category) ? { category } : {}),
    ...(tag !== undefined && TAG_PATTERN.test(tag) ? { tag } : {}),
    ...(kind !== undefined && (KINDS as readonly string[]).includes(kind) ? { kind: kind as Kind } : {}),
  };
}

/** Whether any filter narrows the catalogue. */
export function isNarrowed(search: CatalogSearch): boolean {
  return search.q !== undefined || search.category !== undefined || search.tag !== undefined || search.kind !== undefined;
}

/** The server's own filters for a search: `GET /api/recipes?q=&category=&tag=&kind=`. */
export function filtersOf(search: CatalogSearch): RecipeFilters {
  return { q: search.q, category: search.category, tag: search.tag, kind: search.kind };
}

export interface TagCount {
  tag: string;
  count: number;
}

/**
 * The tags the filter offers: every tag of the catalogue, the most used first and then
 * alphabetically, so what narrows the list most is at hand and the order never jumps.
 */
export function tagCounts(recipes: readonly Pick<RecipeSummary, "tags">[]): TagCount[] {
  const counts = new Map<string, number>();
  for (const recipe of recipes) for (const tag of recipe.tags) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  return [...counts]
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag, "en"));
}

/** The recipes of one page of the catalogue, as `pageWindow` numbers them (from 1). */
export function pageOf<T>(items: readonly T[], from: number, to: number): T[] {
  return from < 1 ? [] : items.slice(from - 1, to);
}

/**
 * The words a recipe's version is printed in: `v1.37.4`, with the recipe's own revision after
 * it only when the recipe was fixed without the application changing (`v1.37.4-r2`). A recipe
 * that pins no version (an unavailable one) has none to print.
 */
export function versionLabel(version: string | null | undefined, revision: number | null | undefined): string | null {
  if (version === null || version === undefined || version === "") return null;
  const bare = /^\d/.test(version) ? `v${version}` : version;
  return revision !== null && revision !== undefined && revision > 1 ? `${bare}-r${String(revision)}` : bare;
}

/**
 * The day of a recipe's last change (`2026-10-06`), as a date in the reader's own calendar. A
 * day has no zone: built from its parts, it never moves to the evening before in the west.
 */
export function dayOf(value: string | null | undefined): Date | null {
  const match = value === null || value === undefined ? null : /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (match === null) return null;
  const [, year = "", month = "", day = ""] = match;
  const date = new Date(Number(year), Number(month) - 1, Number(day));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** What a recipe asks the server to provide, by the name the API gives it, for the catalog key. */
export const DEPENDENCY_LABELS: Readonly<Record<string, PlainKey>> = {
  postgres: "catalog.dependencies.postgres",
  mysql: "catalog.dependencies.mysql",
  mariadb: "catalog.dependencies.mariadb",
  redis: "catalog.dependencies.redis",
  valkey: "catalog.dependencies.valkey",
  mongodb: "catalog.dependencies.mongodb",
  s3: "catalog.dependencies.s3",
};

/**
 * Where an installed recipe stands, as one state the page draws: the catalogue is ahead and
 * can be deployed (`behind`), it is the same (`current`), or the recipe cannot be updated
 * (left the catalogue, or is listed as unavailable) (`stuck`).
 */
export type InstalledState = "behind" | "current" | "stuck";

export function installedState(item: Pick<InstalledRecipe, "update_available" | "reason" | "latest_version">): InstalledState {
  if (item.update_available) return "behind";
  if (item.reason !== null && item.reason !== undefined && item.reason !== "") return "stuck";
  if (item.latest_version === null || item.latest_version === undefined) return "stuck";
  return "current";
}

/**
 * A note of a recipe as it can be read before the application exists: the notes are written
 * with the placeholders the install fills in (`{{ url }}`, `{{ domain }}`, `{{ vars.NAME }}`),
 * which here become what they will stand for, `https://<domain>` and `<domain>`, so the page
 * shows what the operator will be told rather than template syntax.
 */
export function previewNote(note: string): string {
  return note.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_match, name: string) => (name === "url" ? "https://<domain>" : `<${name.replace(/^vars\./, "")}>`));
}

/** How a variable gets its value, for the word a table says it in. */
export const ORIGIN_LABELS: Readonly<Record<string, PlainKey>> = {
  generated: "catalog.detail.origin.generated",
  deployment: "catalog.detail.origin.deployment",
  provisioned: "catalog.detail.origin.provisioned",
  composed: "catalog.detail.origin.composed",
  default: "catalog.detail.origin.default",
  required: "catalog.detail.origin.required",
};
