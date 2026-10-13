/**
 * What an API token can reach, as the create form holds it and as the request needs it. A token
 * bound to a team, a project or an environment sees that and nothing else of the server; the
 * rule is the server's (`bound_kind`), this only turns the choice into `team`, `project` and
 * `environment`.
 */

export type ReachKind = "all" | "team" | "project" | "environment";

export interface TokenReach {
  kind: ReachKind;
  team: string;
  project: string;
  environment: string;
}

export const NO_REACH: TokenReach = { kind: "all", team: "", project: "", environment: "" };

/** Whether the choice names everything it needs to. */
export function reachReady(reach: TokenReach): boolean {
  switch (reach.kind) {
    case "all":
      return true;
    case "team":
      return reach.team !== "";
    case "project":
      return reach.team !== "" && reach.project !== "";
    case "environment":
      return reach.team !== "" && reach.project !== "" && reach.environment !== "";
  }
}

/** The fields the token request takes for this choice: none for everything its owner can reach. */
export function reachBody(reach: TokenReach): { team?: string; project?: string; environment?: string } {
  switch (reach.kind) {
    case "all":
      return {};
    case "team":
      return { team: reach.team };
    case "project":
      return { team: reach.team, project: reach.project };
    case "environment":
      return { team: reach.team, project: reach.project, environment: reach.environment };
  }
}
