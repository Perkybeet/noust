/**
 * Where a new application, site or database goes: which team, project and environment, with
 * the defaults the server would use (the team the operator works in, the team's default
 * project, production), and when there is nothing to ask. Pure, so each rule is tested
 * without rendering a form.
 */

import type { Project, ProjectEnvironment } from "../../api/queries/teams";
import { describeScope } from "./organization";

export interface Place {
  team: string;
  project: string;
  environment: string;
}

export interface Named {
  slug: string;
  name: string;
}

export interface DestinationInput {
  /** The teams a new resource may go to: those the principal may create applications in. */
  teams: readonly Named[];
  projects: readonly Project[];
  environments: readonly ProjectEnvironment[];
  /** The team in view, or null for every team. */
  active: string | null;
  /** What the operator chose in the form so far. */
  choice?: Partial<Place>;
  /** Where the form was asked to start (a link from a project's page, an application being moved). */
  initial?: Partial<Place>;
}

export interface DestinationView {
  /** There is something to choose between: the fields are drawn, and the request names the place. */
  needed: boolean;
  showTeam: boolean;
  teams: Named[];
  projects: Named[];
  environments: Named[];
  /** Null when nothing can be chosen (no team to put it in). */
  value: Place | null;
}

const DEFAULT = "default";
const PRODUCTION = "production";

function inOrder<T extends { slug: string }>(items: readonly T[], first: string): T[] {
  return [...items].sort((a, b) => Number(b.slug === first) - Number(a.slug === first));
}

function pick<T extends { slug: string }>(items: readonly T[], wanted: string | null | undefined, fallback: string): T | undefined {
  return items.find((item) => item.slug === wanted) ?? items.find((item) => item.slug === fallback) ?? items[0];
}

/**
 * The place a form offers, from what the console knows and what the operator chose. A choice
 * that no longer fits (a project of the team that was just changed) falls back to the team's
 * default project and production, never to something that does not exist.
 */
export function resolveDestination({ teams, projects, environments, active, choice = {}, initial = {} }: DestinationInput): DestinationView {
  const slugs = new Set(teams.map((team) => team.slug));
  const inTeams = projects.filter((project) => slugs.has(project.team));
  const scope = describeScope({
    teams: teams.map((team) => ({ id: 0, slug: team.slug, name: team.name, created_at: 0 })),
    projects: inTeams,
    environments: environments.filter((environment) => slugs.has(environment.team)),
    active,
  });
  const needed = scope.showTeam || scope.showProject || scope.showEnvironment;

  const team = pick(teams, choice.team ?? initial.team ?? active, DEFAULT);
  if (team === undefined) return { needed, showTeam: scope.showTeam, teams: [...teams], projects: [], environments: [], value: null };

  const ofTeam = inOrder(inTeams.filter((project) => project.team === team.slug), DEFAULT);
  const project = pick(ofTeam, choice.project ?? initial.project, DEFAULT);
  const ofProject = project === undefined ? [] : inOrder(environments.filter((environment) => environment.team === team.slug && environment.project === project.slug), PRODUCTION);
  const environment = pick(ofProject, choice.environment ?? initial.environment, PRODUCTION);

  return {
    needed,
    showTeam: scope.showTeam,
    teams: [...teams],
    projects: ofTeam.map((item) => ({ slug: item.slug, name: item.name })),
    environments: ofProject.map((item) => ({ slug: item.slug, name: item.name })),
    value: project === undefined || environment === undefined ? null : { team: team.slug, project: project.slug, environment: environment.slug },
  };
}

/** What the request carries: nothing when there was nothing to choose, so the server decides as it always did. */
export function destinationBody(view: DestinationView): { team?: string; project?: string; environment?: string } {
  if (!view.needed || view.value === null) return {};
  return { team: view.value.team, project: view.value.project, environment: view.value.environment };
}
