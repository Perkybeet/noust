/**
 * The applications of the list drawn in groups, for the views of the Applications column (4.2): by project and by
 * state. Pure, so the order and the membership are tested without rendering a table. The table inside each group is
 * the one table of applications (`AppsTable`): a view only decides which rows go under which heading.
 */

import { appStatus } from "../../components/page/status";
import { ownerOf } from "../teams/organization";
import type { Labels } from "../teams/organization";
import type { AppInfo } from "./data";

/** The four things a person asks of the whole list: what is wrong, what is changing, what serves and what is stopped. */
export type StateGroup = "problems" | "deploying" | "running" | "stopped" | "unknown";

/** Worst first: the group that needs a person leads, the quiet ones follow. */
export const STATE_GROUP_ORDER: readonly StateGroup[] = ["problems", "deploying", "running", "stopped", "unknown"];

/** Which group an application is in, by the state language the console speaks (`appStatus`). */
export function stateGroupOf(app: Pick<AppInfo, "status">): StateGroup {
  switch (appStatus(app.status).state) {
    case "failed":
    case "warning":
      return "problems";
    case "deploying":
    case "queued":
      return "deploying";
    case "running":
    case "static":
      return "running";
    case "stopped":
      return "stopped";
    case "unknown":
      return "unknown";
  }
}

export interface StateGroups {
  group: StateGroup;
  apps: AppInfo[];
}

/** The applications by state, in the order of `STATE_GROUP_ORDER`, leaving out a group nothing is in. */
export function groupByState(apps: readonly AppInfo[]): StateGroups[] {
  return STATE_GROUP_ORDER.map((group) => ({ group, apps: apps.filter((app) => stateGroupOf(app) === group) })).filter((entry) => entry.apps.length > 0);
}

export interface ProjectGroup {
  /** `team/project`, or `""` for applications nobody owns (an older node). */
  key: string;
  team: string | null;
  project: string | null;
  /** The name the operator gave the project, or "" for the applications nobody owns. */
  name: string;
  apps: AppInfo[];
}

/**
 * The applications by project, alphabetical by the project's name, and the applications no project holds last. A team
 * is told apart by `key`, since two teams may each have a project of the same name.
 */
export function groupByProject(apps: readonly AppInfo[], labels: Pick<Labels, "project">): ProjectGroup[] {
  const found = new Map<string, ProjectGroup>();
  for (const app of apps) {
    const { team, project } = ownerOf(app);
    const key = team === null || project === null ? "" : `${team}/${project}`;
    let group = found.get(key);
    if (group === undefined) {
      group = { key, team, project, name: team === null || project === null ? "" : labels.project(team, project), apps: [] };
      found.set(key, group);
    }
    group.apps.push(app);
  }
  return [...found.values()].sort((a, b) => (a.key === "" ? 1 : b.key === "" ? -1 : a.name.localeCompare(b.name, undefined, { sensitivity: "base" })));
}
