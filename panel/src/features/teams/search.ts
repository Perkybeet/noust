/**
 * The search params of the pages of teams and projects, and of the lists that say where each
 * resource lives. In the URL, so a filtered view can be shared and reached with Back.
 */

function text(value: unknown, max = 200): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, max);
}

/** A slug as the server makes them: lower-case letters, digits and dashes. Anything else is not one. */
export function slugParam(value: unknown): string | undefined {
  const found = text(value, 64);
  return found !== undefined && /^[a-z0-9][a-z0-9-]*$/.test(found) ? found : undefined;
}

/** `?project=web&environment=staging`: the two filters every list that says where a row lives shares. */
export interface OwnerSearch {
  project?: string;
  environment?: string;
}

/** The two filters of a list, read from its search params; anything malformed is left out. */
export function ownerSearch(search: Record<string, unknown>): OwnerSearch {
  const project = slugParam(search["project"]);
  const environment = slugParam(search["environment"]);
  return { ...(project !== undefined ? { project } : {}), ...(environment !== undefined ? { environment } : {}) };
}

export interface ProjectsSearch {
  q?: string;
  /** Narrows to one team when several are in view. */
  team?: string;
  /** Opens "New project" on arrival, the way the command palette asks for it. */
  new?: true;
}

export function validateProjectsSearch(search: Record<string, unknown>): ProjectsSearch {
  const q = text(search["q"]);
  const team = slugParam(search["team"]);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(team !== undefined ? { team } : {}),
    ...(search["new"] === true || search["new"] === "true" ? { new: true as const } : {}),
  };
}

export interface ProjectSearch {
  /** The environment shown, by slug; the project's first when absent or unknown. */
  env?: string;
}

export function validateProjectSearch(search: Record<string, unknown>): ProjectSearch {
  const env = slugParam(search["env"]);
  return env !== undefined ? { env } : {};
}

export interface TeamsSearch {
  q?: string;
  /** Opens "New team" on arrival, the way the command palette asks for it. */
  new?: true;
}

export function validateTeamsSearch(search: Record<string, unknown>): TeamsSearch {
  const q = text(search["q"]);
  return { ...(q !== undefined ? { q } : {}), ...(search["new"] === true || search["new"] === "true" ? { new: true as const } : {}) };
}
