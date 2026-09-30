/**
 * The data browser's place, in the URL: which table, which view of it, its order and its
 * filters (`?schema=public&table=orders&sort=id:desc&filter=status:eq:paid`), so a filtered
 * table can be bookmarked and shared. The page within it is not: a link opens its first page.
 */

export const OPERATORS = ["eq", "neq", "lt", "lte", "gt", "gte", "like", "ilike", "in", "null", "notnull"] as const;
export type Operator = (typeof OPERATORS)[number];

/** Operators that take no value. */
export const UNARY: ReadonlySet<Operator> = new Set(["null", "notnull"]);

export const PAGE_SIZES = [50, 100, 500] as const;
export type PageSize = (typeof PAGE_SIZES)[number];
export const DEFAULT_PAGE_SIZE: PageSize = 50;

export interface DataSearch {
  schema?: string;
  table?: string;
  /** The table's structure instead of its rows. */
  view?: "structure";
  /** `column:asc` or `column:desc`. */
  sort?: string;
  /** `column:op:value`, as the API takes them. */
  filter?: string[];
  limit?: PageSize;
  /** Redis: a glob the key scan matches. */
  match?: string;
  /** Redis: only keys of this type. */
  type?: string;
}

/** A change to the place: a key set to undefined is cleared. */
export type DataSearchPatch = { [K in keyof DataSearch]?: DataSearch[K] | undefined };

export interface Filter {
  column: string;
  op: Operator;
  value: string;
}

function text(value: unknown, max = 200): string | undefined {
  if (typeof value !== "string") return undefined;
  return value === "" ? undefined : value.slice(0, max);
}

/** A filter as the URL spells it, or null when it is not one. */
export function parseFilter(raw: string): Filter | null {
  const first = raw.indexOf(":");
  if (first <= 0) return null;
  const rest = raw.slice(first + 1);
  const second = rest.indexOf(":");
  const op = (second === -1 ? rest : rest.slice(0, second)) as Operator;
  if (!OPERATORS.includes(op)) return null;
  return { column: raw.slice(0, first), op, value: second === -1 ? "" : rest.slice(second + 1) };
}

export function formatFilter(filter: Filter): string {
  return UNARY.has(filter.op) ? `${filter.column}:${filter.op}` : `${filter.column}:${filter.op}:${filter.value}`;
}

/** An order as the URL spells it, or null. */
export function parseSort(raw: string | undefined): { column: string; descending: boolean } | null {
  if (raw === undefined) return null;
  const at = raw.lastIndexOf(":");
  if (at <= 0) return { column: raw, descending: false };
  const direction = raw.slice(at + 1);
  if (direction !== "asc" && direction !== "desc") return { column: raw, descending: false };
  return { column: raw.slice(0, at), descending: direction === "desc" };
}

/**
 * Reads the search params, dropping anything malformed instead of failing: a hand-edited or
 * stale link still opens the browser, just less filtered.
 */
export function validateDataSearch(search: Record<string, unknown>): DataSearch {
  const schema = text(search["schema"], 128);
  const table = text(search["table"], 128);
  const sort = text(search["sort"], 140);
  const match = text(search["match"], 1024);
  const type = text(search["type"], 16);
  const rawFilters = Array.isArray(search["filter"]) ? search["filter"] : typeof search["filter"] === "string" ? [search["filter"]] : [];
  const filters = rawFilters
    .flatMap((item: unknown) => (typeof item === "string" ? [item.slice(0, 600)] : []))
    .filter((item) => parseFilter(item) !== null)
    .slice(0, 12);
  const limit = Number(search["limit"]);
  return {
    ...(schema !== undefined ? { schema } : {}),
    ...(table !== undefined ? { table } : {}),
    ...(search["view"] === "structure" ? { view: "structure" as const } : {}),
    ...(sort !== undefined ? { sort } : {}),
    ...(filters.length > 0 ? { filter: filters } : {}),
    ...((PAGE_SIZES as readonly number[]).includes(limit) && limit !== DEFAULT_PAGE_SIZE ? { limit: limit as PageSize } : {}),
    ...(match !== undefined ? { match } : {}),
    ...(type !== undefined && /^[a-z]+$/.test(type) ? { type } : {}),
  };
}
