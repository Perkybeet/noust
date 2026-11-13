/**
 * What the lists of the Docker zone keep in their address, so a filtered list can be shared and the link of an application
 * (`/server/docker/containers?app=shop.example.com`) lands on its containers. Read apart from the pages (a route's options are
 * loaded with the first page and its component is not): importing a page here would bring the whole zone with it.
 */

/** The kinds of owner a list is filtered by: an application, a database engine, Noust itself, or nobody (Noust did not create it). */
export const OWNER_FILTERS = ["app", "database", "noust", "nobody"] as const;
export type OwnerFilter = (typeof OWNER_FILTERS)[number];

/** The states a container list is filtered by: what an operator asks for, not every word Docker has. */
export const STATE_FILTERS = ["running", "problem", "stopped"] as const;
export type StateFilter = (typeof STATE_FILTERS)[number];

export interface DockerSearch {
  /** The words the name or the image contains. */
  q?: string | undefined;
  owner?: OwnerFilter | undefined;
  state?: StateFilter | undefined;
  /** Only the containers, volumes... of this application (its domain). */
  app?: string | undefined;
  /** Only this Compose project. */
  project?: string | undefined;
  /** Only the images that have a new version. */
  news?: true | undefined;
  /** The server chosen in the filter of the fleet's list, kept in the address. */
  server?: string | undefined;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;
}

/** Reads a list's address: what it does not know is left out, and nothing here throws on a made-up one. */
export function validateDockerSearch(search: Record<string, unknown>): DockerSearch {
  const q = text(search["q"]);
  const owner = oneOf(search["owner"], OWNER_FILTERS);
  const state = oneOf(search["state"], STATE_FILTERS);
  const app = text(search["app"]);
  const project = text(search["project"]);
  const server = text(search["server"]);
  const news = search["news"] === true || search["news"] === "true" || search["news"] === 1;
  return {
    ...(q !== undefined ? { q } : {}),
    ...(owner !== undefined ? { owner } : {}),
    ...(state !== undefined ? { state } : {}),
    ...(app !== undefined ? { app } : {}),
    ...(project !== undefined ? { project } : {}),
    ...(server !== undefined ? { server } : {}),
    ...(news ? { news: true as const } : {}),
  };
}

/** What a container's own page keeps in its address: whether its console is open, so "Open console" in a menu lands on it. */
export interface ContainerSearch {
  console?: true | undefined;
}

export function validateContainerSearch(search: Record<string, unknown>): ContainerSearch {
  return search["console"] === true || search["console"] === "true" || search["console"] === 1 ? { console: true } : {};
}
