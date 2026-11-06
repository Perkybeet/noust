import { describe, expect, it } from "vitest";

import type { AppInfo } from "./data";
import { STATE_GROUP_ORDER, groupByProject, groupByState, stateGroupOf } from "./groups";

const app = (domain: string, status: string, owner?: { team: string; project: string }): AppInfo =>
  ({ domain, status, app_type: "nextjs", ...(owner !== undefined ? { team: owner.team, project: owner.project, environment: "production" } : {}) }) as unknown as AppInfo;

const NAMES: Readonly<Record<string, string>> = { "a/shop": "Shop", "a/blog": "Blog", "b/shop": "Shop", "a/api": "api" };
const labels = { project: (team: string, project: string) => NAMES[`${team}/${project}`] ?? project };

describe("the applications by state", () => {
  it("puts what is wrong first, then what is changing, what serves and what is stopped", () => {
    const apps = [app("a", "running"), app("b", "stopped"), app("c", "failed"), app("d", "deploying"), app("e", "static"), app("f", "no_answer")];
    expect(groupByState(apps).map((entry) => [entry.group, entry.apps.map((one) => one.domain)])).toEqual([
      ["problems", ["c", "f"]],
      ["deploying", ["d"]],
      ["running", ["a", "e"]],
      ["stopped", ["b"]],
    ]);
  });

  it("leaves out a group nothing is in, and has every state in one group", () => {
    expect(groupByState([app("a", "running")]).map((entry) => entry.group)).toEqual(["running"]);
    expect(groupByState([])).toEqual([]);
    for (const status of ["running", "failed", "stopped", "static", "deploying", "unknown", "restarting", "running_unmanaged"]) {
      expect(STATE_GROUP_ORDER, status).toContain(stateGroupOf({ status }));
    }
  });

  it("keeps a service that keeps restarting, or runs outside Noust, among those that need a person", () => {
    expect(stateGroupOf({ status: "running_unmanaged" })).toBe("problems");
  });
});

describe("the applications by project", () => {
  it("groups by the project and sorts the projects by the name the operator gave them", () => {
    const apps = [app("one", "running", { team: "a", project: "shop" }), app("two", "running", { team: "a", project: "blog" }), app("three", "running", { team: "a", project: "shop" })];
    expect(groupByProject(apps, labels).map((group) => [group.name, group.apps.map((one) => one.domain)])).toEqual([
      ["Blog", ["two"]],
      ["Shop", ["one", "three"]],
    ]);
  });

  it("tells two teams' projects of the same name apart", () => {
    const apps = [app("one", "running", { team: "a", project: "shop" }), app("two", "running", { team: "b", project: "shop" })];
    expect(groupByProject(apps, labels).map((group) => group.key)).toEqual(["a/shop", "b/shop"]);
  });

  it("puts what no project holds last, whatever its name would sort to", () => {
    const apps = [app("loose", "running"), app("held", "running", { team: "a", project: "api" })];
    expect(groupByProject(apps, labels).map((group) => group.key)).toEqual(["a/api", ""]);
  });
});
