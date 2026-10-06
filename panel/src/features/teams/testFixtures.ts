/**
 * A server with teams in it, for the tests of the pages that say where things live: the
 * default team and `acme`, projects and environments, and the applications, sites and
 * databases in them, as the API lists them.
 */

import type { SessionInfo } from "../../api/queries/auth";
import type { Project, ProjectEnvironment, Team } from "../../api/queries/teams";
import { SESSION, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

export const DEFAULT_TEAM: Team = { id: 1, slug: "default", name: "Default", role: null, created_at: 1_790_000_000, created_by: "cli" };
export const ACME: Team = { id: 2, slug: "acme", name: "Acme Corp", role: "owner", created_at: 1_790_100_000, created_by: "ana" };

const project = (id: number, team: string, slug: string, name: string, environments: string[]): Project => ({ id, team, slug, name, environments, created_at: 1_790_000_000, created_by: "cli" });
const environment = (id: number, team: string, projectSlug: string, slug: string, name: string): ProjectEnvironment => ({ id, team, project: projectSlug, slug, name, created_at: 1_790_000_000, created_by: "cli" });

export const PROJECTS: Project[] = [
  project(1, "default", "default", "Default", ["production"]),
  project(2, "acme", "default", "Default", ["production"]),
  project(3, "acme", "web", "Web", ["production", "staging"]),
];

export const ENVIRONMENTS: ProjectEnvironment[] = [
  environment(1, "default", "default", "production", "Production"),
  environment(2, "acme", "default", "production", "Production"),
  environment(3, "acme", "web", "production", "Production"),
  environment(4, "acme", "web", "staging", "Staging"),
];

const where = (team: string, project: string, environment: string) => ({ team, project, environment });

/** Every application listed, each with where it lives. */
export const APPS_IN_TEAMS = [
  { domain: "shop.example.com", name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "inplace", ...where("acme", "web", "production") },
  { domain: "staging.example.com", name: "staging", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3001, layout: "inplace", ...where("acme", "web", "staging") },
  { domain: "admin.example.com", name: "admin", app_type: "python", status: "failed", active: false, enabled: true, port: 8000, layout: "inplace", ...where("default", "default", "production") },
];

export const SITES_IN_TEAMS = [
  { name: "shop.example.com", webserver: "nginx", enabled: true, config_path: "/etc/nginx/sites-available/shop.example.com", has_ssl: true, server_names: ["shop.example.com"], noust_managed: true, app: "shop.example.com", site_name: "shop.example.com", ...where("acme", "web", "production") },
  { name: "admin.example.com", webserver: "nginx", enabled: true, config_path: "/etc/nginx/sites-available/admin.example.com", has_ssl: false, server_names: ["admin.example.com"], noust_managed: true, app: "admin.example.com", site_name: "admin.example.com", ...where("default", "default", "production") },
];

export const DATABASES_IN_TEAMS = [
  { name: "shop_production", engine: "postgresql", size: "46.0 MB", tracked: true, app: "shop.example.com", apps: ["shop.example.com"], ...where("acme", "web", "production") },
  { name: "admin_db", engine: "postgresql", size: "2.0 MB", tracked: true, app: "admin.example.com", apps: ["admin.example.com"], ...where("default", "default", "production") },
];

export interface OrganisationOptions {
  teams?: Team[];
  projects?: Project[];
  environments?: ProjectEnvironment[];
  /** The team the account works in, as the server remembers it. */
  active?: string | null;
  seesAll?: boolean;
}

/** The answers every page that asks about teams needs, for a server with several. */
export function organisation({ teams = [DEFAULT_TEAM, ACME], projects = PROJECTS, environments = ENVIRONMENTS, active = null, seesAll = true }: OrganisationOptions = {}): Record<string, RouteHandler> {
  return {
    "GET /api/teams/mine": () => json(200, { teams, active, sees_all: seesAll }),
    "GET /api/teams": () => json(200, { teams, total: teams.length }),
    "GET /api/projects": (call) => {
      const team = call.search.get("team");
      const rows = team === null ? projects : projects.filter((candidate) => candidate.team === team);
      return json(200, { projects: rows, total: rows.length });
    },
    "GET /api/environments": () => json(200, { environments, total: environments.length }),
    "GET /api/teams/roles": () =>
      json(200, {
        roles: [
          { role: "viewer", permissions: ["self", "apps.read", "databases.read", "backups.read", "teams.read"] },
          { role: "operator", permissions: ["self", "apps.read", "apps.operate", "apps.deploy", "databases.read", "backups.read", "backups.run", "teams.read"] },
          { role: "admin", permissions: ["self", "apps.read", "apps.operate", "apps.deploy", "apps.manage", "secrets.reveal", "databases.read", "databases.write", "databases.manage", "backups.read", "backups.run", "backups.manage", "teams.read"] },
          { role: "owner", permissions: ["self", "apps.read", "apps.operate", "apps.deploy", "apps.manage", "secrets.reveal", "databases.read", "databases.write", "databases.manage", "backups.read", "backups.run", "backups.manage", "teams.read", "teams.manage"] },
        ],
        descriptions: {},
      }),
    ...Object.fromEntries(
      teams.map((team) => [
        `GET /api/teams/${team.slug}`,
        () =>
          json(200, {
            ...team,
            resources: { apps: 2, sites: 1, databases: 1, backup_schedules: 0 },
            members: 2,
            projects: projects.filter((candidate) => candidate.team === team.slug).length,
          }),
      ]),
    ),
  };
}

/** Applications, sites and databases that say where they live. */
export function resourcesInTeams(): Record<string, RouteHandler> {
  return {
    "GET /api/apps": () => json(200, { total: APPS_IN_TEAMS.length, apps: APPS_IN_TEAMS }),
    "GET /api/sites": () => json(200, { sites: SITES_IN_TEAMS, total: SITES_IN_TEAMS.length, webserver: "nginx" }),
    "GET /api/databases/databases": () => json(200, { databases: DATABASES_IN_TEAMS, total: DATABASES_IN_TEAMS.length, problems: [] }),
  };
}

/** The server's roles as `GET /api/auth/roles` lists them, for an account's per-team permissions. */
export function accountRoles(): Record<string, RouteHandler> {
  return {
    "GET /api/auth/roles": () =>
      json(200, {
        roles: {
          viewer: ["self", "apps.read", "databases.read", "backups.read", "teams.read"],
          operator: ["self", "apps.read", "apps.operate", "apps.deploy", "databases.read", "backups.read", "backups.run", "teams.read"],
          admin: ["self", "apps.read", "apps.operate", "apps.deploy", "apps.manage", "databases.read", "databases.write", "databases.manage", "backups.read", "teams.read", "teams.manage"],
        },
        permissions: {},
      }),
  };
}

/** A session of an account that only reads: no permission to organise anything. */
export function viewerSession(username = "vic"): SessionInfo {
  return {
    ...SESSION,
    grant: null,
    role: "viewer",
    account: { username, role: "viewer" } as NonNullable<SessionInfo["account"]>,
    permissions: ["self", "apps.read", "databases.read", "backups.read", "teams.read"],
  };
}

/**
 * An administrator's account in sudo mode, as a person who has just confirmed is: it sees every
 * team, may organise them, and no "Confirm it's you" opens on top of a dialog. It is an account,
 * so the team it works in is the one the server remembers for it.
 */
export function governingSession(): SessionInfo {
  return {
    ...SESSION,
    elevated_until: "2999-01-01T00:00:00+00:00",
    role: "admin",
    account: { username: "ana", role: "admin" } as NonNullable<SessionInfo["account"]>,
    permissions: [...(SESSION.permissions ?? []), "teams.read", "teams.manage"],
  };
}

/** The routes of a signed-in page on a server with teams: the shell's, the organisation's and the resources'. */
export function teamsRoutes(options: OrganisationOptions = {}, session: SessionInfo = governingSession()): Record<string, RouteHandler> {
  return { ...signedInRoutes(session), ...organisation(options), ...resourcesInTeams(), ...accountRoles() };
}
