/**
 * How the console talks about where things live: team, project, environment. Pure, so every
 * rule is tested without rendering a page.
 *
 * What a list row says is three slugs (`team`, `project`, `environment`, from the API: an
 * application's, a site's, a database's). A server nobody organised has one team, one project
 * and one environment, and then nothing about this is drawn at all: `describeScope` is where
 * that is decided, once, for every list, filter and form.
 */

import type { Project, ProjectEnvironment, Team } from "../../api/queries/teams";

/** What a row of a list may say about where it lives; absent on a server older than teams. */
export interface OwnedRow {
  team?: string | null | undefined;
  project?: string | null | undefined;
  environment?: string | null | undefined;
}

export interface Owner {
  team: string | null;
  project: string | null;
  environment: string | null;
}

/** The three slugs of a row, null where it names none. */
export function ownerOf(row: OwnedRow): Owner {
  return { team: row.team ?? null, project: row.project ?? null, environment: row.environment ?? null };
}

/** Whether the store knows whose a row is: a site file or a database nobody recorded has no team. */
export function isOwned(row: OwnedRow): boolean {
  return (row.team ?? null) !== null;
}

/** What narrows a list. A project or an environment is a slug, matched the way the server matches it. */
export interface OwnerFilter {
  team?: string | null | undefined;
  project?: string | null | undefined;
  environment?: string | null | undefined;
}

/**
 * The rows that live where the filter says, in their order. A row nobody owns is left out of
 * any narrowed list: it is not in that team, project or environment, and saying it is would be
 * wrong; it still shows when nothing narrows.
 */
export function filterByOwner<T extends OwnedRow>(rows: readonly T[], filter: OwnerFilter): T[] {
  const { team = null, project = null, environment = null } = filter;
  if (team === null && project === null && environment === null) return [...rows];
  return rows.filter((row) => {
    const owner = ownerOf(row);
    if (team !== null && owner.team !== team) return false;
    if (project !== null && owner.project !== project) return false;
    if (environment !== null && owner.environment !== environment) return false;
    return true;
  });
}

/**
 * How many rows each place holds: under `team/project` and under `team/project/environment`.
 * Rows that nobody owns are in no place.
 */
export function countByOwner(rows: readonly OwnedRow[]): Map<string, number> {
  const counts = new Map<string, number>();
  const bump = (key: string): void => {
    counts.set(key, (counts.get(key) ?? 0) + 1);
  };
  for (const row of rows) {
    const { team, project, environment } = ownerOf(row);
    if (team === null || project === null) continue;
    bump(`${team}/${project}`);
    if (environment !== null) bump(`${team}/${project}/${environment}`);
  }
  return counts;
}

export interface EnvironmentGroup<T> {
  environment: string;
  rows: T[];
}

/**
 * A project's rows, one group per environment in the project's own order, the empty ones
 * included: an environment with nothing in it is still a place to put something.
 */
export function groupByEnvironment<T extends OwnedRow>(rows: readonly T[], team: string, project: string, environments: readonly string[]): EnvironmentGroup<T>[] {
  const here = filterByOwner(rows, { team, project });
  return environments.map((environment) => ({ environment, rows: here.filter((row) => row.environment === environment) }));
}

/** The names a row's slugs are shown with, falling back to the slug when a name is not known. */
export interface Labels {
  team: (slug: string) => string;
  project: (team: string, slug: string) => string;
  environment: (team: string, project: string, slug: string) => string;
}

export function buildLabels(teams: readonly Team[], projects: readonly Project[], environments: readonly ProjectEnvironment[]): Labels {
  const teamNames = new Map(teams.map((team) => [team.slug, team.name]));
  const projectNames = new Map(projects.map((project) => [`${project.team}/${project.slug}`, project.name]));
  const environmentNames = new Map(environments.map((environment) => [`${environment.team}/${environment.project}/${environment.slug}`, environment.name]));
  return {
    team: (slug) => teamNames.get(slug) ?? slug,
    project: (team, slug) => projectNames.get(`${team}/${slug}`) ?? slug,
    environment: (team, project, slug) => environmentNames.get(`${team}/${project}/${slug}`) ?? slug,
  };
}

export interface ScopeInput {
  teams: readonly Team[];
  projects: readonly Project[];
  environments: readonly ProjectEnvironment[];
  /** The team in view, or null for every team the principal sees. */
  active: string | null;
}

/** What the console offers to tell apart in the scope in view. */
export interface Scope {
  projects: readonly Project[];
  environments: readonly ProjectEnvironment[];
  /** Several teams are in view at once, so a row says which one it is in. */
  showTeam: boolean;
  /** More than one project in view: a filter and a column are worth their room. */
  showProject: boolean;
  /** More than one environment (by slug) in view. */
  showEnvironment: boolean;
  /** Any of the three: a server nobody organised draws none of it. */
  where: boolean;
}

/**
 * What there is to choose between in the scope in view: the team the operator works in, or all
 * of them. One team with one project and one environment has nothing to choose, and the
 * console draws no filter, no column and no field for it, so a server nobody organised looks
 * exactly as it always did.
 */
export function describeScope({ teams, projects, environments, active }: ScopeInput): Scope {
  const inView = active === null ? projects : projects.filter((project) => project.team === active);
  const places = new Set(inView.map((project) => `${project.team}/${project.slug}`));
  const hereEnvironments = environments.filter((environment) => places.has(`${environment.team}/${environment.project}`));
  const showTeam = active === null && teams.length > 1;
  const showProject = inView.length > 1;
  const showEnvironment = new Set(hereEnvironments.map((environment) => environment.slug)).size > 1;
  return { projects: inView, environments: hereEnvironments, showTeam, showProject, showEnvironment, where: showTeam || showProject || showEnvironment };
}

/** The environments to offer for a project, a team or everything, each slug once, in order. */
export function optionsFor(environments: readonly ProjectEnvironment[], within: { team?: string | null; project?: string | null }): { slug: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const environment of environments) {
    if (within.team != null && environment.team !== within.team) continue;
    if (within.project != null && environment.project !== within.project) continue;
    if (!seen.has(environment.slug)) seen.set(environment.slug, environment.name);
  }
  return [...seen].map(([slug, name]) => ({ slug, name }));
}

/** A project's page, as the router builds it. */
export function projectPath(project: { team: string; slug: string }): { to: "/projects/$team/$project"; params: { team: string; project: string } } {
  return { to: "/projects/$team/$project", params: { team: project.team, project: project.slug } };
}

/** What a team holds, by kind, from the counts its detail carries (`GET /api/teams/{team}`). */
export interface Held {
  apps: number;
  sites: number;
  databases: number;
  schedules: number;
}

export function heldBy(resources: Record<string, number> | undefined): Held {
  return {
    apps: resources?.["apps"] ?? 0,
    sites: resources?.["sites"] ?? 0,
    databases: resources?.["databases"] ?? 0,
    schedules: resources?.["backup_schedules"] ?? 0,
  };
}
