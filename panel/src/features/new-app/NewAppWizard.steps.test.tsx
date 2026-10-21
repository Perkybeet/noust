/**
 * The wizard's click budget (checklist M-12 and M-19): the steps with nothing to ask are passed
 * over, a recipe is one step with a Deploy of its own, and the ways in open on the right place.
 * `e2e/click-budget.spec.ts` counts the same flows in a browser against the real backend.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { RECIPES, WORDPRESS, recipeVariable } from "./testFixtures";
import type { Inspection } from "./wizard";

/** A small service: one type detected, nothing declared. Nothing is left to ask after the address. */
const PLAIN: Inspection = {
  app_type: "nodejs",
  detected_types: ["nodejs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm start",
  default_port: 3000,
  env_keys: [],
  branch: "main",
  commit: "a1b2c3d",
  compatible: true,
};

const APP_TYPES = {
  types: [
    { type: "nextjs", name: "Next.js", default_port: 3000 },
    { type: "nodejs", name: "Node.js", default_port: 3000 },
    { type: "php-fpm", name: "PHP (PHP-FPM)", default_port: 0 },
  ],
};

const JOB = {
  id: "5a1c0de0",
  type: "deploy",
  name: "Deploy blog.example.com",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Downloading the source",
  created_at: "2026-09-28T10:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "blog.example.com" },
};

function wizard(path: string, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "GET /api/recipes": () => json(200, RECIPES),
    "GET /api/recipes/wordpress": () => json(200, WORDPRESS),
    "POST /api/apps/inspect": () => json(200, PLAIN),
    "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Queued", job: JOB }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  });
  return { backend, harness: renderConsole(path) };
}

type User = ReturnType<typeof renderConsole>["user"];

async function inspectPlain(user: User): Promise<void> {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.type(screen.getByLabelText("Repository or directory"), "https://github.com/acme/api.git");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { level: 2, name: "Address" });
}

describe("a public repository to its first build", () => {
  it("is Inspect, Continue and Deploy: configuration and variables have nothing to ask", { timeout: 30_000 }, async () => {
    const { backend, harness } = wizard("/apps/new");
    const { user } = harness;
    await inspectPlain(user);
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // The step after the address is the deploy itself.
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toHaveFocus();
    expect(screen.queryByRole("heading", { level: 2, name: "Configuration" })).not.toBeInTheDocument();
    // What was passed over is said, and is where the facts are: the stepper lists what asked, and
    // each line of the summary opens the step it came from.
    expect(screen.getByText(/Everything below was detected from the source/)).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Steps" });
    expect(within(steps).queryByRole("button", { name: /^Configuration/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change detected" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change variables" })).toBeInTheDocument();
    expect(screen.getByText("Node.js with npm")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("button", { name: "Deploy blog.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({
      domain: "blog.example.com",
      source: "https://github.com/acme/api.git",
      app_type: "nodejs",
      layout: "releases",
      ssl: true,
      // shop.example.com holds 3000 on the fake machine: the next free port after it.
      port: 3001,
    });
  });

  it("opens a passed-over step from the stepper, and goes on from it to the deploy", { timeout: 30_000 }, async () => {
    const { harness } = wizard("/apps/new");
    const { user } = harness;
    await inspectPlain(user);
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Deploy" });

    await user.click(screen.getByRole("button", { name: "Change detected" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Port"));
    await user.type(screen.getByLabelText("Port"), "3050");
    // It was opened by the operator, so Continue goes on from it: to the deploy, not back to the address.
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Deploy" });
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("3050")).toBeInTheDocument();
  });

  it("goes Back from the deploy to the address, over the steps it passed", { timeout: 30_000 }, async () => {
    const { harness } = wizard("/apps/new");
    const { user } = harness;
    await inspectPlain(user);
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Deploy" });
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
    expect(screen.getByLabelText("Domain")).toHaveValue("blog.example.com");
  });

  it("still asks for configuration when more than one type matches, and for a variable that has no value", { timeout: 30_000 }, async () => {
    const { harness } = wizard("/apps/new", {
      "POST /api/apps/inspect": () =>
        json(200, {
          ...PLAIN,
          app_type: "nextjs",
          detected_types: ["nextjs", "nodejs"],
          env_keys: [{ name: "API_TOKEN", default: null, secret: true, required: true }],
        }),
    });
    const { user } = harness;
    await inspectPlain(user);
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Variables" });
    expect(screen.queryByText(/Everything below was detected from the source/)).not.toBeInTheDocument();
  });

  it("does not ask about a database the project does not name, even where an engine runs here", { timeout: 30_000 }, async () => {
    const { harness } = wizard("/apps/new", {
      "GET /api/databases/engines": () =>
        json(200, {
          engines: [
            {
              name: "postgresql",
              display_name: "PostgreSQL",
              installed: true,
              running: true,
              version: "16",
              capabilities: ["tables", "sql", "dump"],
              container: null,
            },
          ],
        }),
    });
    const { user } = harness;
    await inspectPlain(user);
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    // The project names no database, so the step is not asked: Deploy follows the address.
    await screen.findByRole("heading", { level: 2, name: "Deploy" });
    expect(screen.queryByRole("heading", { level: 2, name: "Database" })).not.toBeInTheDocument();
  });
});

describe("the ways in the wizard opens on", () => {
  it("opens on the recipes for ?start=recipe, without a click to choose them", { timeout: 30_000 }, async () => {
    wizard("/apps/new?start=recipe");
    await screen.findByRole("heading", { level: 1, name: "New application" });
    expect(await screen.findByRole("button", { name: "From a recipe" })).toHaveAttribute("aria-current", "true");
    expect(await screen.findByRole("button", { name: "Use WordPress" })).toBeInTheDocument();
  });

  it("opens on an export for ?start=import", { timeout: 30_000 }, async () => {
    wizard("/apps/new?start=import");
    await screen.findByRole("heading", { level: 1, name: "New application" });
    expect(await screen.findByRole("button", { name: "Import an application" })).toHaveAttribute("aria-current", "true");
  });

  it("opens on the default for a start it does not know", { timeout: 30_000 }, async () => {
    wizard("/apps/new?start=nonsense");
    await screen.findByRole("heading", { level: 1, name: "New application" });
    expect(await screen.findByRole("radio", { name: "URL or path" })).toBeChecked();
  });
});

describe("a recipe to its first build", () => {
  /** WordPress with one variable it cannot run without. */
  const NEEDS_EMAIL = { ...WORDPRESS, variables: [recipeVariable("ADMIN_EMAIL", { origin: "required", required: true }), ...WORDPRESS.variables] };

  it("is one choice and one Deploy, and Deploy waits for the variables the recipe cannot run without", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard("/apps/new?start=recipe", { "GET /api/recipes/wordpress": () => json(200, NEEDS_EMAIL) });
    const { user } = harness;
    await user.click(await screen.findByRole("button", { name: "Use WordPress" }));
    await screen.findByRole("heading", { level: 2, name: "Address and variables" });

    // The variable the recipe needs is in front, not folded, and asked for by name.
    const field = screen.getByLabelText(/^ADMIN_EMAIL/);
    expect(field).toBeVisible();
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Deploy" }));

    // Blocked, and says why in a sentence that leads to the field.
    expect(backend.callsTo("POST /api/apps")).toHaveLength(0);
    const notice = await screen.findByText(/needs a value before it can deploy/);
    expect(notice).toBeInTheDocument();
    expect(screen.getByText("The recipe has no default for this one: it needs a value.")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));

    // The sentence names the field and takes the operator to it.
    await user.click(screen.getByRole("button", { name: "Go to ADMIN_EMAIL" }));
    expect(field).toHaveFocus();

    await user.type(field, "ops@example.com");
    expect(screen.queryByText("The recipe has no default for this one: it needs a value.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({
      domain: "blog.example.com",
      recipe: "wordpress",
      env_vars: { ADMIN_EMAIL: "ops@example.com" },
    });
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
  });

  it("goes to the field that is missing first, and counts the others", { timeout: 40_000 }, async () => {
    const two = {
      ...WORDPRESS,
      variables: [recipeVariable("ADMIN_EMAIL", { origin: "required", required: true }), recipeVariable("LICENCE_KEY", { origin: "required", required: true }), ...WORDPRESS.variables],
    };
    const { harness } = wizard("/apps/new?start=recipe", { "GET /api/recipes/wordpress": () => json(200, two) });
    const { user } = harness;
    await user.click(await screen.findByRole("button", { name: "Use WordPress" }));
    await screen.findByRole("heading", { level: 2, name: "Address and variables" });
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(await screen.findByText(/2 variables need a value before it can deploy, starting with/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText(/^ADMIN_EMAIL/)).toHaveFocus();
    });
  });

  it("deploys a recipe that needs nothing in one Deploy, with the domain typed", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard("/apps/new?start=recipe");
    const { user } = harness;
    await user.click(await screen.findByRole("button", { name: "Use WordPress" }));
    await screen.findByRole("heading", { level: 2, name: "Address and variables" });
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
  });
});
