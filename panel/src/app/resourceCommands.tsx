/**
 * What the command palette finds besides pages and applications: the databases, sites,
 * certificates, services and recent deployments of the server on screen (checklist M-23).
 *
 * The index is the console's own lists, read once when the palette opens and kept by the query
 * cache (`useResourceIndex`); nothing here fetches. Each resource becomes one `Command` in its
 * own group, named the way its page names it, found by every name it answers to, and opening the
 * page that shows it. Pure, so every rule is tested without a palette.
 */

import { Cog, Database as DatabaseIcon, FolderKanban, Globe, Rocket, ShieldCheck, Users } from "lucide-react";

import type { CertEntry } from "../api/queries/certs";
import type { Database } from "../api/queries/databases";
import type { DeploymentList } from "../api/queries/deployments";
import type { ServiceList } from "../api/queries/services";
import type { SiteEntry } from "../api/queries/sites";
import type { Project, Team } from "../api/queries/teams";
import { deployStatus } from "../components/page/status";
import type { T } from "../i18n";
import { engineName } from "../features/databases/engines";
import { serviceState } from "../features/services/data";
import { teamRoleLabel } from "../features/teams/roles";
import type { Command } from "./CommandPalette";

export type ServiceEntry = ServiceList["services"][number];
export type DeploymentEntry = DeploymentList["items"][number];

/** How many of the newest deployments the palette keeps: the ones someone looks for by name. */
export const RECENT_DEPLOYMENTS = 30;

/** The lists the palette indexes; each is absent until its read arrives, or when it is not the operator's to read. */
export interface ResourceIndex {
  databases?: readonly Database[] | undefined;
  sites?: readonly SiteEntry[] | undefined;
  certificates?: readonly CertEntry[] | undefined;
  services?: readonly ServiceEntry[] | undefined;
  deployments?: readonly DeploymentEntry[] | undefined;
  /** The teams and projects the principal sees, from the console's own organisation (not read by the palette). */
  teams?: readonly Team[] | undefined;
  projects?: readonly Project[] | undefined;
}

/** Where a command goes: a route of the console, its path parameters and its search. */
export interface NavTarget {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, unknown>;
}

export interface ResourceContext {
  t: T;
  navigate: (target: NavTarget) => void;
}

function words(...parts: readonly (string | null | undefined)[]): string {
  return parts.filter((part): part is string => part !== null && part !== undefined && part !== "").join(" ");
}

function databaseCommands(databases: readonly Database[], { navigate }: ResourceContext): Command[] {
  return databases.map((database) => ({
    id: `database:${database.engine}:${database.name}`,
    group: "Databases",
    label: database.name,
    detail: engineName(database.engine),
    icon: <DatabaseIcon />,
    keywords: words(database.engine, engineName(database.engine), database.username, database.owner, database.app, ...(database.apps ?? [])),
    kind: "navigate",
    run: () => navigate({ to: "/databases/$engine/$name", params: { engine: database.engine, name: database.name } }),
  }));
}

function siteCommands(sites: readonly SiteEntry[], { navigate }: ResourceContext): Command[] {
  return sites.map((site) => ({
    id: `site:${site.name}`,
    group: "Domains",
    label: site.name,
    detail: site.webserver === "apache" ? "Apache" : "nginx",
    icon: <Globe />,
    // A site answers on every name it lists, which is what an operator types for it.
    keywords: words(...site.server_names, site.app),
    kind: "navigate",
    run: () => navigate({ to: "/domains/sites/$site", params: { site: site.name } }),
  }));
}

function certificateDetail(cert: CertEntry, t: T): string | undefined {
  const days = cert.days_remaining;
  if (days === null || days === undefined) return undefined;
  return days < 0 ? t("shell.commands.certificateExpired") : t("shell.commands.daysLeft", { count: days });
}

function certificateCommands(certificates: readonly CertEntry[], { t, navigate }: ResourceContext): Command[] {
  return certificates.map((cert) => {
    const detail = certificateDetail(cert, t);
    return {
      id: `certificate:${cert.domain}`,
      group: "Certificates",
      label: cert.domain,
      ...(detail !== undefined ? { detail } : {}),
      icon: <ShieldCheck />,
      keywords: words(...cert.domains, cert.issuer),
      kind: "navigate",
      // A certificate has no page of its own: the list, filtered to it, is where it is read.
      run: () => navigate({ to: "/domains", search: { q: cert.domain } }),
    };
  });
}

function serviceCommands(services: readonly ServiceEntry[], { navigate }: ResourceContext): Command[] {
  return services.map((service) => ({
    id: `service:${service.name}`,
    group: "Services",
    label: service.name,
    ...(service.description ? { detail: service.description } : {}),
    icon: <Cog />,
    status: serviceState(service).state,
    keywords: words(service.description),
    kind: "navigate",
    run: () => navigate({ to: "/server/services/$name", params: { name: service.name } }),
  }));
}

function deploymentCommands(deployments: readonly DeploymentEntry[], { t, navigate }: ResourceContext): Command[] {
  return deployments.slice(0, RECENT_DEPLOYMENTS).map((deployment) => {
    const detail = [deployment.commit_message, deployment.git_commit].find((text) => text !== null && text !== undefined && text !== "") ?? undefined;
    return {
      id: `deployment:${deployment.id}`,
      group: "Deployments",
      label: t("shell.commands.deployment", { domain: deployment.domain, id: String(deployment.id) }),
      ...(detail !== undefined ? { detail } : {}),
      icon: <Rocket />,
      status: deployStatus(deployment.status, t.locale).state,
      keywords: words(deployment.domain, deployment.git_commit, deployment.git_branch, deployment.commit_message, deployment.status, deployment.triggered_by),
      kind: "navigate",
      run: () => navigate({ to: "/apps/$domain/deployments/$id", params: { domain: deployment.domain, id: String(deployment.id) } }),
    };
  });
}

function teamCommands(teams: readonly Team[], { t, navigate }: ResourceContext): Command[] {
  return teams.map((team) => ({
    id: `team:${team.slug}`,
    group: "Teams",
    label: team.name,
    ...(team.role !== null && team.role !== undefined ? { detail: teamRoleLabel(t, team.role) } : {}),
    icon: <Users />,
    keywords: words(team.slug),
    kind: "navigate",
    run: () => navigate({ to: "/teams/$team", params: { team: team.slug } }),
  }));
}

function projectCommands(projects: readonly Project[], teams: readonly Team[], { navigate }: ResourceContext): Command[] {
  const names = new Map(teams.map((team) => [team.slug, team.name]));
  return projects.map((project) => ({
    id: `project:${project.team}/${project.slug}`,
    group: "Projects",
    label: project.name,
    // Which team it is in, only where there are several to tell two of one name apart.
    ...(teams.length > 1 ? { detail: names.get(project.team) ?? project.team } : {}),
    icon: <FolderKanban />,
    keywords: words(project.slug, project.team, names.get(project.team), ...(project.environments ?? [])),
    kind: "navigate",
    run: () => navigate({ to: "/projects/$team/$project", params: { team: project.team, project: project.slug } }),
  }));
}

/** One command per resource of the index, group by group; a list that has not arrived adds nothing. */
export function resourceCommands(index: ResourceIndex, context: ResourceContext): Command[] {
  return [
    ...projectCommands(index.projects ?? [], index.teams ?? [], context),
    ...teamCommands(index.teams ?? [], context),
    ...databaseCommands(index.databases ?? [], context),
    ...siteCommands(index.sites ?? [], context),
    ...certificateCommands(index.certificates ?? [], context),
    ...serviceCommands(index.services ?? [], context),
    ...deploymentCommands(index.deployments ?? [], context),
  ];
}
