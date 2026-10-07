import { describe, expect, it } from "vitest";

import type { Project, ProjectEnvironment } from "../../api/queries/teams";
import { destinationBody, resolveDestination } from "./destination";

const project = (team: string, slug: string, environments: string[]): Project => ({ id: 0, team, slug, name: slug, environments, created_at: 0 });
const environment = (team: string, projectSlug: string, slug: string): ProjectEnvironment => ({ id: 0, team, project: projectSlug, slug, name: slug, created_at: 0 });

const TEAMS = [
  { slug: "default", name: "Default" },
  { slug: "acme", name: "Acme" },
];
const PROJECTS = [project("default", "default", ["production"]), project("acme", "mobile", ["production"]), project("acme", "default", ["production", "staging"]), project("acme", "web", ["production"])];
const ENVIRONMENTS = [
  environment("default", "default", "production"),
  environment("acme", "mobile", "production"),
  environment("acme", "default", "staging"),
  environment("acme", "default", "production"),
  environment("acme", "web", "production"),
];

describe("where a new resource goes", () => {
  it("asks nothing on a server nobody organised and sends nothing", () => {
    const view = resolveDestination({ teams: TEAMS.slice(0, 1), projects: PROJECTS.slice(0, 1), environments: ENVIRONMENTS.slice(0, 1), active: null });

    expect(view.needed).toBe(false);
    expect(view.value).toEqual({ team: "default", project: "default", environment: "production" });
    expect(destinationBody(view)).toEqual({});
  });

  it("asks for the team when several are in view, starting from the default team", () => {
    const view = resolveDestination({ teams: TEAMS, projects: PROJECTS, environments: ENVIRONMENTS, active: null });

    expect(view.needed).toBe(true);
    expect(view.showTeam).toBe(true);
    expect(view.value).toEqual({ team: "default", project: "default", environment: "production" });
    expect(destinationBody(view)).toEqual({ team: "default", project: "default", environment: "production" });
  });

  it("works in the team the operator is on and starts at its default project, production first", () => {
    const view = resolveDestination({ teams: TEAMS, projects: PROJECTS, environments: ENVIRONMENTS, active: "acme" });

    expect(view.showTeam).toBe(false);
    expect(view.value).toEqual({ team: "acme", project: "default", environment: "production" });
    expect(view.projects.map((p) => p.slug)).toEqual(["default", "mobile", "web"]);
    expect(view.environments.map((e) => e.slug)).toEqual(["production", "staging"]);
    expect(view.needed).toBe(true);
  });

  it("starts where it was asked to, when that place exists", () => {
    const view = resolveDestination({ teams: TEAMS, projects: PROJECTS, environments: ENVIRONMENTS, active: "acme", initial: { project: "default", environment: "staging" } });

    expect(view.value).toEqual({ team: "acme", project: "default", environment: "staging" });
  });

  it("falls back, never to a place that does not exist", () => {
    const view = resolveDestination({ teams: TEAMS, projects: PROJECTS, environments: ENVIRONMENTS, active: "acme", choice: { project: "gone", environment: "nowhere" } });

    expect(view.value).toEqual({ team: "acme", project: "default", environment: "production" });
  });

  it("follows a change of team: the project and environment chosen for the other do not carry over", () => {
    const view = resolveDestination({ teams: TEAMS, projects: PROJECTS, environments: ENVIRONMENTS, active: null, choice: { team: "acme", project: "web" } });
    expect(view.value).toEqual({ team: "acme", project: "web", environment: "production" });

    const back = resolveDestination({ teams: TEAMS, projects: PROJECTS, environments: ENVIRONMENTS, active: null, choice: { team: "default", project: "web" } });
    expect(back.value).toEqual({ team: "default", project: "default", environment: "production" });
  });

  it("asks for the project alone when one team has several and the other only its default", () => {
    const view = resolveDestination({ teams: TEAMS.slice(1), projects: PROJECTS.slice(1), environments: ENVIRONMENTS.slice(1), active: "acme" });

    expect(view.needed).toBe(true);
  });

  it("has nowhere to put anything when the principal may create in no team", () => {
    const view = resolveDestination({ teams: [], projects: PROJECTS, environments: ENVIRONMENTS, active: null });

    expect(view.value).toBeNull();
    expect(destinationBody(view)).toEqual({});
  });
});
