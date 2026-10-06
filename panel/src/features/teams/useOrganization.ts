import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { chooseActiveTeam, environmentsQuery, myTeamsQuery, projectsQuery, teamKeys, teamRolesQuery } from "../../api/queries/teams";
import type { Project, ProjectEnvironment, Team } from "../../api/queries/teams";
import { useConsoleContext, useNode } from "../../nodes/useNode";
import { rolesQuery } from "../settings/accounts/api";
import { holds } from "./access";
import { useLocalActiveTeam, writeLocalActiveTeam } from "./activeTeam";
import { buildLabels, describeScope } from "./organization";
import type { Labels, Scope } from "./organization";

export interface Organization {
  /**
   * The server answered with its teams. False while they are being read and on a server that
   * has none to give (a node older than teams): then nothing about teams is drawn, and no
   * list is narrowed.
   */
  available: boolean;
  /**
   * The teams, projects and environments are still being read for the first time. A list that
   * shows where its rows live waits for this as it waits for its rows, so the columns and filters
   * arrive with them and nothing is pushed sideways when they do.
   */
  loading: boolean;
  /** Why they could not be read, when they could not: the server's own words are shown as it gave them. */
  error: unknown;
  /** Reads them again. */
  reload: () => void;
  teams: readonly Team[];
  /** No team is hidden from the principal. */
  seesAll: boolean;
  /** The team in view, or null for every team the principal sees. */
  active: string | null;
  projects: readonly Project[];
  environments: readonly ProjectEnvironment[];
  /** What there is to tell apart in the team in view. */
  scope: Scope;
  labels: Labels;
  /**
   * Whether more than one team is there to work in or, with exactly one, it is not the
   * default one: a server nobody organised draws no team switcher.
   */
  teamsMatter: boolean;
  /** Whether the principal holds a permission anywhere, or in one team. */
  can: (permission: string, team?: string | null) => boolean;
  /** Works in a team (null: every team in view); an account's choice is remembered by the server. */
  choose: (team: string | null) => Promise<void>;
  choosing: boolean;
}

const NO_TEAMS: readonly Team[] = [];
const NO_PROJECTS: readonly Project[] = [];
const NO_ENVIRONMENTS: readonly ProjectEnvironment[] = [];

/**
 * The teams, projects and environments of the server on screen, the team the operator works
 * in, and what they may do in each: one place that every list, filter, form and switcher asks,
 * so they cannot disagree. Nothing is read on the fleet's and the central's own pages, which
 * are no server's.
 */
export function useOrganization(): Organization {
  const queryClient = useQueryClient();
  const { node } = useNode();
  const context = useConsoleContext();
  const enabled = context.kind === "server";
  // A server that has no teams to give answers once: asking again on every mount would flip the pages that wait on it between loading and not.
  const options = { enabled, retry: false, retryOnMount: false } as const;
  const session = useQuery({ ...sessionQuery(), enabled });
  const mine = useQuery({ ...myTeamsQuery(), ...options });
  const projects = useQuery({ ...projectsQuery(), ...options, enabled: enabled && mine.isSuccess });
  const environments = useQuery({ ...environmentsQuery(), ...options, enabled: enabled && mine.isSuccess });
  const roles = useQuery({ ...rolesQuery(), ...options });
  const teamRoles = useQuery({ ...teamRolesQuery(), ...options });
  const local = useLocalActiveTeam(node);

  const available = mine.isSuccess;
  const teams = mine.data?.teams ?? NO_TEAMS;
  const account = session.data?.account != null;
  // An account's choice is the server's (it follows the person to another browser); a
  // credential with no account has only this browser to remember it in.
  const chosen = account ? (mine.data?.active ?? null) : local;
  const active = chosen !== null && teams.some((team) => team.slug === chosen) ? chosen : null;
  const projectList = projects.data?.projects ?? NO_PROJECTS;
  const environmentList = environments.data?.environments ?? NO_ENVIRONMENTS;

  const scope = useMemo(
    () => describeScope({ teams, projects: projectList, environments: environmentList, active }),
    [teams, projectList, environmentList, active],
  );
  const labels = useMemo(() => buildLabels(teams, projectList, environmentList), [teams, projectList, environmentList]);

  const access = useMemo(
    () => ({
      session: session.data,
      rolePermissions: roles.data?.roles,
      teamRoles: Object.fromEntries(teams.map((team) => [team.slug, team.role])),
      teamRolePermissions: teamRoles.data === undefined ? undefined : Object.fromEntries(teamRoles.data.roles.map((entry) => [entry.role, entry.permissions])),
    }),
    [session.data, roles.data, teams, teamRoles.data],
  );
  const can = useCallback((permission: string, team: string | null = null) => holds(access, permission, team), [access]);

  const choosing = useMutation({
    mutationFn: async (team: string | null) => {
      if (account) {
        const answer = await chooseActiveTeam(team);
        queryClient.setQueryData(teamKeys.mine, answer);
      } else {
        writeLocalActiveTeam(node, team);
      }
    },
  });
  const choose = useCallback((team: string | null) => choosing.mutateAsync(team), [choosing]);

  const teamsMatter = available && (teams.length > 1 || teams.some((team) => team.slug !== "default"));
  return {
    available,
    loading: enabled && (mine.isPending || (mine.isSuccess && (projects.isPending || environments.isPending))),
    error: mine.error,
    reload: () => void mine.refetch(),
    teams,
    seesAll: mine.data?.sees_all ?? false,
    active,
    projects: projectList,
    environments: environmentList,
    scope,
    labels,
    teamsMatter,
    can,
    choose,
    choosing: choosing.isPending,
  };
}
