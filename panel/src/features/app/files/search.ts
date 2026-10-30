/**
 * The Files page's search parameter, in a module of its own: the route reads it when the
 * address changes, and a route's options are loaded with the first page, so they must not
 * import the page and its editor with it.
 */

/** A path cleaned for use as a search parameter: no leading or trailing slash, no empty parts. */
export function cleanFolder(path: unknown): string {
  if (typeof path !== "string") return "";
  return path
    .split("/")
    .filter((part) => part !== "" && part !== "." && part !== "..")
    .join("/")
    .slice(0, 1024);
}

export interface FilesSearch {
  path?: string;
}

export function validateFilesSearch(search: Record<string, unknown>): FilesSearch {
  const path = cleanFolder(search["path"]);
  return path === "" ? {} : { path };
}
