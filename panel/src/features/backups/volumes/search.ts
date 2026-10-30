/**
 * The Volumes page's search parameter, in a module of its own: a route's options are loaded
 * with the first page, so the address must be read without importing the page and its drawers.
 */

export interface VolumesSearch {
  /** The application whose data is open in the drawer. */
  domain?: string;
}

export function validateVolumesSearch(search: Record<string, unknown>): VolumesSearch {
  const domain = typeof search["domain"] === "string" && search["domain"] !== "" ? search["domain"].slice(0, 253) : undefined;
  return domain !== undefined ? { domain } : {};
}
