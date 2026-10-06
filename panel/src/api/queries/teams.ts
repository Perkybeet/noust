/**
 * Teams, their members, projects and environments (`/api/teams`, `/api/projects`,
 * `/api/environments`), and moving an application between them. Every rule - who may see a
 * team, the last owner who cannot leave, what an empty project is - is the server's; the
 * console asks and shows the answer, and a refusal is shown as the server worded it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type MyTeams = ResponseOf<"/api/teams/mine", "get">;
export type Team = MyTeams["teams"][number];
export type TeamDetail = ResponseOf<"/api/teams/{team}", "get">;
export type TeamRoles = ResponseOf<"/api/teams/roles", "get">;
export type Members = ResponseOf<"/api/teams/{team}/members", "get">;
export type Member = Members["members"][number];
export type ProjectList = ResponseOf<"/api/projects", "get">;
export type Project = ProjectList["projects"][number];
export type EnvironmentList = ResponseOf<"/api/environments", "get">;
export type ProjectEnvironment = EnvironmentList["environments"][number];
export type MoveBody = BodyOf<"/api/apps/{domain}/move", "post">;
export type MoveResult = ResponseOf<"/api/apps/{domain}/move", "post">;

/**
 * The four roles a team has, weakest first; the server lists them too (`/api/teams/roles`), and
 * this is only the order and the words the console has catalogs for.
 */
export const TEAM_ROLES = ["viewer", "operator", "admin", "owner"] as const;
export type TeamRole = (typeof TEAM_ROLES)[number];

/** Whether a role the server answered is one this console has words for. */
export function isTeamRole(role: string | null | undefined): role is TeamRole {
  return TEAM_ROLES.some((known) => known === role);
}

/**
 * Keys of the team queries. Everything about teams is under `["teams"]`, projects under
 * `["projects"]` and environments under `["environments"]`, so a change invalidates the
 * prefix and every list that names where a resource lives is read again.
 */
export const teamKeys = {
  all: ["teams"] as const,
  mine: ["teams", "mine"] as const,
  roles: ["teams", "roles"] as const,
  detail: (team: string) => ["teams", "detail", team] as const,
  members: (team: string) => ["teams", "detail", team, "members"] as const,
  projects: ["projects"] as const,
  projectsOf: (team: string | null) => ["projects", { team }] as const,
  environments: ["environments"] as const,
  environmentsOf: (team: string | null, project: string | null) => ["environments", { team, project }] as const,
};

/** The teams the signed-in principal sees, its role in each and the one it works in. */
export const myTeamsQuery = () =>
  queryOptions({
    queryKey: teamKeys.mine,
    queryFn: ({ signal }) => request("get", "/api/teams/mine", { signal }),
  });

/** What each team role holds, weakest first, with each permission described. */
export const teamRolesQuery = () =>
  queryOptions({
    queryKey: teamKeys.roles,
    queryFn: ({ signal }) => request("get", "/api/teams/roles", { signal }),
    staleTime: Infinity,
  });

/** One team with what it holds. A team the principal does not see answers 404, like one that is not there. */
export const teamQuery = (team: string) =>
  queryOptions({
    queryKey: teamKeys.detail(team),
    queryFn: ({ signal }) => request("get", "/api/teams/{team}", { params: { team }, signal }),
  });

export const teamMembersQuery = (team: string) =>
  queryOptions({
    queryKey: teamKeys.members(team),
    queryFn: ({ signal }) => request("get", "/api/teams/{team}/members", { params: { team }, signal }),
  });

/** Every project the principal sees, with its environments' slugs, or one team's. */
export const projectsQuery = (team: string | null = null) =>
  queryOptions({
    queryKey: team === null ? teamKeys.projects : teamKeys.projectsOf(team),
    queryFn: ({ signal }) => request("get", "/api/projects", { ...(team !== null ? { query: { team } } : {}), signal }),
  });

/** Every environment the principal sees, with its name, or those of one team or project. */
export const environmentsQuery = (team: string | null = null, project: string | null = null) =>
  queryOptions({
    queryKey: team === null && project === null ? teamKeys.environments : teamKeys.environmentsOf(team, project),
    queryFn: ({ signal }) =>
      request("get", "/api/environments", {
        query: { ...(team !== null ? { team } : {}), ...(project !== null ? { project } : {}) },
        signal,
      }),
  });

export function createTeam(name: string, slug?: string) {
  return request("post", "/api/teams", { body: { name, ...(slug !== undefined ? { slug } : {}) } });
}

export function renameTeam(team: string, name: string) {
  return request("patch", "/api/teams/{team}", { params: { team }, body: { name } });
}

export function deleteTeam(team: string) {
  return request("delete", "/api/teams/{team}", { params: { team } });
}

/** Chooses the team the principal works in; null forgets the choice. Only an account has one to remember. */
export function chooseActiveTeam(team: string | null) {
  return request("put", "/api/teams/active", { body: { team } });
}

export function addMember(team: string, username: string, role: TeamRole) {
  return request("post", "/api/teams/{team}/members", { params: { team }, body: { username, role } });
}

export function setMemberRole(team: string, username: string, role: TeamRole) {
  return request("patch", "/api/teams/{team}/members/{username}", { params: { team, username }, body: { role } });
}

export function removeMember(team: string, username: string) {
  return request("delete", "/api/teams/{team}/members/{username}", { params: { team, username } });
}

export function createProject(team: string, name: string, slug?: string) {
  return request("post", "/api/teams/{team}/projects", { params: { team }, body: { name, ...(slug !== undefined ? { slug } : {}) } });
}

export function renameProject(team: string, project: string, name: string) {
  return request("patch", "/api/teams/{team}/projects/{project}", { params: { team, project }, body: { name } });
}

export function deleteProject(team: string, project: string) {
  return request("delete", "/api/teams/{team}/projects/{project}", { params: { team, project } });
}

export function createEnvironment(team: string, project: string, name: string, slug?: string) {
  return request("post", "/api/teams/{team}/projects/{project}/environments", {
    params: { team, project },
    body: { name, ...(slug !== undefined ? { slug } : {}) },
  });
}

export function renameEnvironment(team: string, project: string, environment: string, name: string) {
  return request("patch", "/api/teams/{team}/projects/{project}/environments/{environment}", {
    params: { team, project, environment },
    body: { name },
  });
}

export function deleteEnvironment(team: string, project: string, environment: string) {
  return request("delete", "/api/teams/{team}/projects/{project}/environments/{environment}", {
    params: { team, project, environment },
  });
}

/** Moves an application with its sites, databases and backup schedule; nothing is restarted. */
export function moveApp(domain: string, body: MoveBody) {
  return request("post", "/api/apps/{domain}/move", { params: { domain }, body });
}
