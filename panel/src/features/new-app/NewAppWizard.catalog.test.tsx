import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { NOT_FOUND, VAULTWARDEN_FULL, catalogueRoutes, full } from "../catalog/testFixtures";
import { recipeVariable } from "./testFixtures";

/** The log viewer sizes its window from the layout, which jsdom does not have. */
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
});

const APP_TYPES = { types: [{ type: "docker-compose", name: "Docker Compose", default_port: 0 }] };

function job(domain: string) {
  return {
    id: "5a1c0de0",
    type: "deploy",
    name: `Deploy ${domain}`,
    description: "Deploying",
    status: "running",
    progress: 10,
    total_steps: 100,
    current_step: "Pulling the images",
    created_at: "2026-10-06T10:21:13",
    deployment_id: null,
    logs: [{ timestamp: "2026-10-06T10:21:14", level: "INFO", message: "Writing the stack's files" }],
    metadata: { domain },
  };
}

function wizard(path: string, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...catalogueRoutes(),
    "POST /api/apps": (call) => json(202, { job_id: "5a1c0de0", status: "pending", message: "Queued", job: job((call.body as { domain: string }).domain) }),
    "GET /api/jobs/5a1c0de0": () => json(200, job("rss.example.com")),
    ...extra,
  });
  return { backend, harness: renderConsole(path) };
}

describe("the wizard opened on a recipe the catalog chose", () => {
  it("is on the recipe's one step at once, with what it cannot run without in front and Deploy waiting for it", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard("/apps/new?start=recipe&recipe=freshrss");
    const { user } = harness;
    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    expect(backend.callsTo("GET /api/recipes/freshrss")).toHaveLength(1);

    // What the recipe is, above where it answers: its version and that it runs in Docker.
    expect(screen.getByText("v1.30.1")).toBeInTheDocument();
    expect(screen.getByText("Runs in Docker")).toBeInTheDocument();

    // The variable it needs is a field of its own, with what it is for, and the way to it.
    const email = screen.getByLabelText(/^ADMIN_EMAIL/);
    expect(screen.getByText("The e-mail address of the administrator account.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Domain"), "rss.example.com");
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(await screen.findByText(/needs a value before it can deploy/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Go to ADMIN_EMAIL" }));
    expect(email).toHaveFocus();
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.type(email, "ops@example.com");
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ domain: "rss.example.com", recipe: "freshrss", env_vars: { ADMIN_EMAIL: "ops@example.com" } });
  });

  it("follows the install as a job, its output printing as it runs", { timeout: 40_000 }, async () => {
    const { harness } = wizard("/apps/new?start=recipe&recipe=vaultwarden");
    const { user } = harness;
    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Domain"), "vault.example.com");
    await user.click(screen.getByRole("button", { name: "Deploy" }));
    expect(await screen.findByText("Deploying vault.example.com")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "View output" }));
    const drawer = await screen.findByRole("dialog", { name: "Deploying vault.example.com" });
    expect(await within(drawer).findByText("Writing the stack's files")).toBeInTheDocument();
  });

  it("shows what a variable is for, the default an empty field keeps, and masks a secret that is asked for", { timeout: 40_000 }, async () => {
    const secret = full(VAULTWARDEN_FULL, {
      variables: [
        ...VAULTWARDEN_FULL.variables,
        recipeVariable("SMTP_PASSWORD", { secret: true, description: "The password of the mail account." }),
      ],
    });
    const { harness } = wizard("/apps/new?start=recipe&recipe=vaultwarden", { "GET /api/recipes/vaultwarden": () => json(200, secret) });
    const { user } = harness;
    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "What it sets up, and optional variables" }));
    const signups = screen.getByLabelText(/SIGNUPS_ALLOWED/);
    expect(signups).toHaveAttribute("placeholder", "true");
    expect(screen.getByText("Whether anybody can create an account. Set it to false once yours exists.")).toBeInTheDocument();
    // The generated ones are listed by name, never asked, never given a value to show.
    expect(screen.getByText("ADMIN_TOKEN")).toBeInTheDocument();
    expect(screen.queryByLabelText(/ADMIN_TOKEN/)).not.toBeInTheDocument();
    // A secret that is asked for is typed masked, and shown only on request.
    const password = screen.getByLabelText(/^SMTP_PASSWORD/);
    expect(password).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show SMTP_PASSWORD" }));
    expect(password).toHaveAttribute("type", "text");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says when the recipe it was sent to does not exist, and stays on the recipes", { timeout: 40_000 }, async () => {
    wizard("/apps/new?start=recipe&recipe=nothing-here", { "GET /api/recipes/nothing-here": NOT_FOUND });
    expect(await screen.findByText("Could not read the nothing-here recipe")).toBeInTheDocument();
    expect(screen.getByText("No recipe called nothing-here")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Use Vaultwarden" })).toBeInTheDocument();
  });

  it("ignores a recipe parameter that cannot be a recipe's name", { timeout: 40_000 }, async () => {
    const { backend } = wizard("/apps/new?start=recipe&recipe=..%2Fetc");
    expect(await screen.findByRole("button", { name: "Use Vaultwarden" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Source" })).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.path.startsWith("/api/recipes/") && call.path !== "/api/recipes/categories")).toHaveLength(0);
  });
});

describe("the recipes inside the wizard", () => {
  it("are the catalog's cards, searched and filtered the same way, with the filters kept in the step", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard("/apps/new?start=recipe");
    const { user } = harness;
    const list = await screen.findByRole("list", { name: "Recipes" });
    // The wizard lists every recipe: it is where one is picked, not browsed.
    expect(await within(list).findByRole("heading", { level: 3, name: "Ghost" })).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "Search recipes" }), "feeds");
    await waitFor(() => expect(backend.callsTo("GET /api/recipes").some((call) => call.search.get("q") === "feeds")).toBe(true));
    expect(await screen.findByRole("heading", { level: 3, name: "FreshRSS" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: "Vaultwarden" })).not.toBeInTheDocument();
    // The wizard's own address keeps saying where it is, not what was searched for.
    expect(harness.location().search).toMatchObject({ start: "recipe" });
    expect(harness.location().search).not.toHaveProperty("q");
    // No page of the recipe in the middle of the wizard: the card only has its Use button.
    expect(screen.queryByRole("link", { name: /^Details of/ })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
