import { configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { DEFAULT_TEAM, ENVIRONMENTS, PROJECTS, organisation, governingSession } from "../teams/testFixtures";
import type { OrganisationOptions } from "../teams/testFixtures";
import type { Inspection } from "./wizard";

const INSPECTION: Inspection = {
  app_type: "nodejs",
  detected_types: ["nodejs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: [],
  start_command: "npm start",
  default_port: 3000,
  env_keys: [],
  branch: "",
  commit: "",
};

const JOB = {
  id: "0dd5e7a1",
  type: "deploy",
  name: "Deploy storefront.example.com",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Deploying",
  created_at: "2026-09-25T19:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "storefront.example.com" },
};

function wizardAt(path: string, options: OrganisationOptions | null) {
  const backend = fakeBackend({
    ...signedInRoutes(governingSession()),
    ...(options === null ? {} : organisation(options)),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nodejs", name: "Node.js", default_port: 3000 }] }),
    "GET /api/domains/dns": () => json(200, { domain: "x", expected_addresses: [], resolved_addresses: [], points_here: true }),
    "POST /api/apps/inspect": () => json(200, INSPECTION),
    "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Deployment queued", job: JOB }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [JOB], total: 1, active: 1 }),
  } satisfies Record<string, RouteHandler>);
  return { backend, harness: renderConsole(path) };
}

async function toAddress(user: ReturnType<typeof renderConsole>["user"]) {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.type(screen.getByLabelText("Repository or directory"), "/var/www/src/storefront");
  await user.click(screen.getByRole("button", { name: "Inspect source" }));
  await screen.findByRole("heading", { level: 2, name: "Address" });
}

// The organisation is read in three steps (the teams, then their projects and environments, then
// the lists), which a busy machine stretches past the second a lookup waits by default.
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

describe("where the new application goes", () => {
  it("asks nothing on a server with one team, one project and one environment, and sends nothing", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizardAt("/apps/new", { teams: [DEFAULT_TEAM], projects: PROJECTS.slice(0, 1), environments: ENVIRONMENTS.slice(0, 1) });
    await toAddress(harness.user);
    expect(screen.queryByText("Where it lives")).not.toBeInTheDocument();
    await harness.user.type(screen.getByLabelText("Domain"), "storefront.example.com");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));
    await harness.user.click(await screen.findByRole("button", { name: "Deploy storefront.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    const body = backend.callsTo("POST /api/apps")[0]?.body as Record<string, unknown>;
    expect(body).not.toHaveProperty("team");
    expect(body).not.toHaveProperty("project");
    expect(body).not.toHaveProperty("environment");
  });

  it("asks nothing on a server that has no teams to give, as an older node has none", async () => {
    const { harness } = wizardAt("/apps/new", null);
    await toAddress(harness.user);
    expect(screen.queryByText("Where it lives")).not.toBeInTheDocument();
  });

  it("asks which project and environment when the team has several, starting from its default and production", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizardAt("/apps/new", { active: "acme" });
    await toAddress(harness.user);
    const place = screen.getByRole("group", { name: "Where it lives" });
    expect(within(place).queryByRole("combobox", { name: "Team" })).not.toBeInTheDocument();
    expect(within(place).getByRole("combobox", { name: "Project" })).toHaveTextContent("Default");
    // Default has one environment: nothing to ask there.
    expect(within(place).queryByRole("combobox", { name: "Environment" })).not.toBeInTheDocument();

    await harness.user.click(within(place).getByRole("combobox", { name: "Project" }));
    await harness.user.click(await screen.findByRole("option", { name: "Web" }));
    await harness.user.click(within(place).getByRole("combobox", { name: "Environment" }));
    await harness.user.click(await screen.findByRole("option", { name: "Staging" }));

    await harness.user.type(screen.getByLabelText("Domain"), "storefront.example.com");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));
    // The wizard says where it will live, with what else was decided.
    expect(await screen.findByText("Web / Staging")).toBeInTheDocument();
    await harness.user.click(screen.getByRole("button", { name: "Deploy storefront.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ team: "acme", project: "web", environment: "staging" });
  });

  it("asks the team too when every team is in view", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizardAt("/apps/new", { active: null });
    await toAddress(harness.user);
    const place = screen.getByRole("group", { name: "Where it lives" });
    expect(within(place).getByRole("combobox", { name: "Team" })).toHaveTextContent("Default");
    await harness.user.click(within(place).getByRole("combobox", { name: "Team" }));
    await harness.user.click(await screen.findByRole("option", { name: "Acme Corp" }));
    // Acme's own projects, from its default.
    expect(within(place).getByRole("combobox", { name: "Project" })).toHaveTextContent("Default");
    await harness.user.type(screen.getByLabelText("Domain"), "storefront.example.com");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));
    await harness.user.click(await screen.findByRole("button", { name: "Deploy storefront.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ team: "acme", project: "default", environment: "production" });
  });

  it("starts where a project's page sent it", async () => {
    const { harness } = wizardAt("/apps/new?team=acme&project=web&environment=staging", { active: "acme" });
    await toAddress(harness.user);
    const place = screen.getByRole("group", { name: "Where it lives" });
    expect(within(place).getByRole("combobox", { name: "Project" })).toHaveTextContent("Web");
    expect(within(place).getByRole("combobox", { name: "Environment" })).toHaveTextContent("Staging");
  });
});
