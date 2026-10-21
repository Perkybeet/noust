/**
 * The API reference's filters, which live in the address so a view of the API can be shared. Apart
 * from the page on purpose: the route reads them to validate a link before the page's code is
 * fetched, and a route that imported the page for this would carry all of it into the first load.
 */

/** The filters, which live in the address so a view of the API can be shared. */
export interface ApiReferenceSearch {
  q?: string;
  tag?: string;
  method?: string;
}

export const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 120);
}

/** Reads the search params, dropping anything malformed: a hand-edited link still opens the list. */
export function validateApiReferenceSearch(search: Record<string, unknown>): ApiReferenceSearch {
  const q = text(search["q"]);
  const tag = text(search["tag"]);
  const method = text(search["method"]);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(tag !== undefined ? { tag } : {}),
    ...(method !== undefined && (METHODS as readonly string[]).includes(method) ? { method } : {}),
  };
}

