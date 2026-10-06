import { describe, expect, it } from "vitest";

import type { Project, ProjectEnvironment, Team } from "../../api/queries/teams";
import { buildLabels, countByOwner, describeScope, filterByOwner, groupByEnvironment, isOwned, optionsFor, ownerOf, projectPath } from "./organization";

const TEAMS: Team[] = [
  { id: 1, slug: "default", name: "Default", role: "owner", created_at: 0 },
  { id: 2, slug: "acme", name: "Acme Corp", role: "viewer", created_at: 0 },
];

const PROJECTS: Project[] = [
  { id: 1, team: "default", slug: "default", name: "Default", environments: ["production"], created_at: 0 },
  { id: 2, team: "acme", slug: "web", name: "Web", environments: ["production", "staging"], created_at: 0 },
  { id: 3, team: "acme", slug: "mobile", name: "Mobile app", environments: ["production"], created_at: 0 },
];

const ENVIRONMENTS: ProjectEnvironment[] = [
  { id: 1, team: "default", project: "default", slug: "production", name: "Production", created_at: 0 },
  { id: 2, team: "acme", project: "web", slug: "production", name: "Production", created_at: 0 },
  { id: 3, team: "acme", project: "web", slug: "staging", name: "Staging", created_at: 0 },
  { id: 4, team: "acme", project: "mobile", slug: "production", name: "Production", created_at: 0 },
];

const row = (domain: string, team: string | null, project: string | null, environment: string | null) => ({ domain, team, project, environment });

const OWNED = row("shop.acme.com", "acme", "web", "production");
const NOBODYS = row("hand-made.example.com", null, null, null);
const ROWS = [
  OWNED,
  row("staging.acme.com", "acme", "web", "staging"),
  row("app.acme.com", "acme", "mobile", "production"),
  row("blog.example.com", "default", "default", "production"),
  NOBODYS,
];

describe("what a row says about where it lives", () => {
  it("reads the three slugs, whatever is missing", () => {
    expect(ownerOf({ team: "acme", project: "web", environment: "staging" })).toEqual({ team: "acme", project: "web", environment: "staging" });
    expect(ownerOf({})).toEqual({ team: null, project: null, environment: null });
    expect(ownerOf({ team: undefined, project: "web" })).toEqual({ team: null, project: "web", environment: null });
  });

  it("knows a row that belongs to nobody", () => {
    expect(isOwned(NOBODYS)).toBe(false);
    expect(isOwned(OWNED)).toBe(true);
  });
});

describe("filtering by where rows live", () => {
  it("keeps every row when nothing narrows", () => {
    expect(filterByOwner(ROWS, {})).toHaveLength(5);
  });

  it("narrows to a team, and leaves out what nobody owns", () => {
    expect(filterByOwner(ROWS, { team: "acme" }).map((r) => r.domain)).toEqual(["shop.acme.com", "staging.acme.com", "app.acme.com"]);
  });

  it("narrows to a project and an environment by slug, like the server does", () => {
    expect(filterByOwner(ROWS, { project: "web" }).map((r) => r.domain)).toEqual(["shop.acme.com", "staging.acme.com"]);
    expect(filterByOwner(ROWS, { environment: "staging" }).map((r) => r.domain)).toEqual(["staging.acme.com"]);
    expect(filterByOwner(ROWS, { team: "acme", project: "web", environment: "production" }).map((r) => r.domain)).toEqual(["shop.acme.com"]);
  });

  it("finds nothing in a place that holds nothing", () => {
    expect(filterByOwner(ROWS, { team: "globex" })).toEqual([]);
  });
});

describe("counting what a place holds", () => {
  it("counts by project and by project and environment", () => {
    const counts = countByOwner(ROWS);
    expect(counts.get("acme/web")).toBe(2);
    expect(counts.get("acme/web/staging")).toBe(1);
    expect(counts.get("default/default/production")).toBe(1);
    expect(counts.get("acme/nowhere")).toBeUndefined();
  });
});

describe("grouping a project's rows by environment", () => {
  it("keeps the project's own order of environments and the empty ones", () => {
    const groups = groupByEnvironment(ROWS, "acme", "web", ["production", "staging", "preview"]);
    expect(groups.map((g) => [g.environment, g.rows.length])).toEqual([
      ["production", 1],
      ["staging", 1],
      ["preview", 0],
    ]);
  });
});

describe("the labels a row's slugs are shown with", () => {
  const labels = buildLabels(TEAMS, PROJECTS, ENVIRONMENTS);

  it("names a team, a project and an environment, and falls back to the slug", () => {
    expect(labels.team("acme")).toBe("Acme Corp");
    expect(labels.project("acme", "mobile")).toBe("Mobile app");
    expect(labels.environment("acme", "web", "staging")).toBe("Staging");
    expect(labels.team("ghost")).toBe("ghost");
    expect(labels.project("acme", "ghost")).toBe("ghost");
    expect(labels.environment("acme", "web", "ghost")).toBe("ghost");
  });
});

describe("what a scope offers to choose from", () => {
  it("offers a team's projects and their environments", () => {
    const scope = describeScope({ projects: PROJECTS, environments: ENVIRONMENTS, teams: TEAMS, active: "acme" });
    expect(scope.projects.map((p) => p.slug)).toEqual(["web", "mobile"]);
    expect(scope.showProject).toBe(true);
    expect(scope.showEnvironment).toBe(true);
    expect(scope.showTeam).toBe(false);
  });

  it("offers the teams too when every one is in view", () => {
    const scope = describeScope({ projects: PROJECTS, environments: ENVIRONMENTS, teams: TEAMS, active: null });
    expect(scope.showTeam).toBe(true);
    expect(scope.projects).toHaveLength(3);
  });

  it("offers nothing to choose on a server nobody organised", () => {
    const scope = describeScope({ projects: PROJECTS.slice(0, 1), environments: ENVIRONMENTS.slice(0, 1), teams: TEAMS.slice(0, 1), active: null });
    expect(scope).toMatchObject({ showTeam: false, showProject: false, showEnvironment: false, where: false });
  });

  it("lists the environments of the chosen project only, each slug once", () => {
    expect(optionsFor(ENVIRONMENTS, { team: "acme", project: "web" }).map((e) => e.slug)).toEqual(["production", "staging"]);
    expect(optionsFor(ENVIRONMENTS, { team: "acme" }).map((e) => e.slug)).toEqual(["production", "staging"]);
    expect(optionsFor(ENVIRONMENTS, {}).map((e) => e.slug)).toEqual(["production", "staging"]);
  });
});

describe("the address of a project", () => {
  it("is the team and the project", () => {
    expect(projectPath({ team: "acme", slug: "web" })).toEqual({ to: "/projects/$team/$project", params: { team: "acme", project: "web" } });
  });
});
