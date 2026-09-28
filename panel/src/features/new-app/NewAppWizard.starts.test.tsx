import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { EXPORT, RECIPES, WORDPRESS } from "./testFixtures";
import type { Inspection } from "./wizard";

const APP_TYPES = {
  types: [
    { type: "nextjs", name: "Next.js", default_port: 3000 },
    { type: "nodejs", name: "Node.js", default_port: 3000 },
    { type: "php-fpm", name: "PHP (PHP-FPM)", default_port: 0 },
  ],
};

function job(domain: string, patch: Record<string, unknown> = {}) {
  return {
    id: "5a1c0de0",
    type: "deploy",
    name: `Deploy ${domain}`,
    description: "Deploying",
    status: "running",
    progress: 10,
    total_steps: 100,
    current_step: "Downloading the source",
    created_at: "2026-09-28T10:21:13",
    deployment_id: null,
    logs: [],
    metadata: { domain },
    ...patch,
  };
}

/**
 * The wizard over a fake backend whose job ends as `finished` says once something was queued,
 * with the deployment row the deployer writes for it.
 */
function wizard(domain: string, finished: Record<string, unknown>, extra: Record<string, RouteHandler> = {}) {
  let queued = false;
  const running = job(domain);
  const accepted = () => {
    queued = true;
    return json(202, { job_id: running.id, status: "pending", message: "Queued", job: running });
  };
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "GET /api/recipes": () => json(200, RECIPES),
    "GET /api/recipes/wordpress": () => json(200, WORDPRESS),
    "POST /api/apps": accepted,
    "POST /api/apps/import": accepted,
    "GET /api/deployments": () =>
      json(200, {
        items: queued ? [{ id: 12, domain, status: "running", triggered_by: "panel", has_log: true, job_id: running.id }] : [],
        total: queued ? 1 : 0,
        next_before_id: null,
      }),
    [`GET /api/jobs/${running.id}`]: () => json(200, queued ? job(domain, { status: "completed", progress: 100, ...finished }) : running),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

async function choose(user: ReturnType<typeof renderConsole>["user"], mode: string) {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.click(await screen.findByRole("radio", { name: mode }));
}

describe("starting from a recipe", () => {
  it("offers the recipes as cards, the unavailable ones marked with the server's reason", { timeout: 20_000 }, async () => {
    const { harness } = wizard("blog.example.com", {});
    await choose(harness.user, "From a recipe");
    const wordpress = (await screen.findByRole("heading", { level: 3, name: "WordPress" })).closest("li") as HTMLElement;
    expect(within(wordpress).getByText("A mysql database")).toBeInTheDocument();
    expect(within(wordpress).getByText("PHP-FPM 7.4 or newer")).toBeInTheDocument();
    const site = within(wordpress).getByRole("link", { name: /wordpress\.org/ });
    expect(site).toHaveAttribute("href", "https://wordpress.org");
    expect(site).toHaveAttribute("target", "_blank");
    expect(site.getAttribute("rel")).toContain("noopener");

    const ghost = screen.getByRole("heading", { level: 3, name: "Ghost" }).closest("li") as HTMLElement;
    expect(within(ghost).getByText("Not available in this release")).toBeInTheDocument();
    expect(within(ghost).getByText(/supports only MySQL 8 in production/)).toBeInTheDocument();
    expect(within(ghost).queryByRole("button")).not.toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("reviews the recipe, deploys it without a source, and ends with its notes as next steps", { timeout: 20_000 }, async () => {
    const notes = [
      "Open https://blog.example.com/wp-admin/install.php now to choose the site title.",
      "Plugins, themes and uploads live in shared/wp-content and survive every release.",
    ];
    const { backend, harness } = wizard("blog.example.com", { result: { deployment_id: 12, recipe: "wordpress", notes } });
    const { user } = harness;
    await choose(user, "From a recipe");
    await user.click(await screen.findByRole("button", { name: "Use WordPress" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Review" })).toHaveFocus();
    expect(backend.callsTo("GET /api/recipes/wordpress")).toHaveLength(1);
    // Generated variables are listed, never asked; the others can be set over the recipe's.
    expect(screen.getByText("Generated for you")).toBeInTheDocument();
    expect(screen.getByText("WORDPRESS_DB_PASSWORD")).toBeInTheDocument();
    expect(screen.queryByLabelText(/WORDPRESS_DB_PASSWORD/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/WP_DEBUG/)).toHaveValue("");
    // What comes with it, read-only.
    expect(screen.getByText("A new mysql database and user, their credentials in the app's .env")).toBeInTheDocument();
    expect(screen.getByText("wp-content")).toBeInTheDocument();

    // A domain already deployed here is refused before anything is sent.
    await user.type(screen.getByLabelText("Domain"), "shop.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(/shop\.example\.com is already deployed/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Domain"));
    await user.type(screen.getByLabelText("Domain"), "blog.example.com");
    await user.type(screen.getByLabelText(/WP_DEBUG/), "1");
    await expectNoAxeViolations(harness.container);
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Deploy blog.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toEqual({
      domain: "blog.example.com",
      recipe: "wordpress",
      app_type: "auto",
      webserver: "nginx",
      ssl: true,
      include_www: false,
      env_vars: { WP_DEBUG: "1" },
      skip_database: false,
    });

    // Followed here to its end, then what to do next: every address in a note is a link.
    expect(await screen.findByText("blog.example.com is deployed", {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Next steps" })).toBeInTheDocument();
    const install = screen.getByRole("link", { name: /https:\/\/blog\.example\.com\/wp-admin\/install\.php/ });
    expect(install).toHaveAttribute("href", "https://blog.example.com/wp-admin/install.php");
    expect(install.getAttribute("rel")).toContain("noopener");
    expect(screen.getByText(/survive every release/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open blog.example.com" })).toHaveAttribute("href", "/apps/blog.example.com");
    expect(screen.getByRole("link", { name: "Follow the build log" })).toHaveAttribute("href", "/apps/blog.example.com/deployments/12");
    expect(harness.location().pathname).toBe("/apps/new");
    await expectNoAxeViolations(harness.container);
  });

  it("says why a recipe could not be read, verbatim", { timeout: 20_000 }, async () => {
    const { harness } = wizard("blog.example.com", {}, { "GET /api/recipes/wordpress": () => problem(404, "not_found", "No recipe named wordpress") });
    await choose(harness.user, "From a recipe");
    await harness.user.click(await screen.findByRole("button", { name: "Use WordPress" }));
    expect(await screen.findByText("No recipe named wordpress")).toBeInTheDocument();
    expect(screen.getByText("Could not read the WordPress recipe")).toBeInTheDocument();
  });
});

describe("importing an application", () => {
  function file(content: string, name = "shop.wasm-app.json") {
    return new File([content], name, { type: "application/json" });
  }

  it("refuses a file that is not an export, clearly, before sending anything", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizard("shop.example.org", {});
    await choose(harness.user, "Import an application");
    await harness.user.upload(screen.getByLabelText("Export file"), file("{ not json", "notes.json"));
    expect(await screen.findByText(/^This file is not JSON\./)).toBeInTheDocument();
    expect(screen.getByLabelText("Export file")).toHaveAttribute("aria-invalid", "true");
    await harness.user.upload(screen.getByLabelText("Export file"), file(JSON.stringify({ ...EXPORT, version: 3 })));
    expect(await screen.findByText(/This export is version 3; this console reads version 1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/import")).toHaveLength(0);
  });

  it("reads the export, asks for what it left out, imports it and lists what was applied", { timeout: 20_000 }, async () => {
    const report = {
      domain: "shop.example.org",
      steps: [
        { part: "deploy shop.example.org", applied: true, detail: "" },
        { part: "alias store.example.org", applied: true, detail: "" },
        { part: "cron nightly", applied: false, detail: "The cron job needs a user that does not exist here" },
      ],
      not_applied: [{ part: "cron nightly", applied: false, detail: "The cron job needs a user that does not exist here" }],
    };
    const { backend, harness } = wizard("shop.example.org", { result: report });
    const { user } = harness;
    await choose(user, "Import an application");
    await user.upload(screen.getByLabelText("Export file"), file(JSON.stringify(EXPORT)));
    expect(await screen.findByText("An export of shop.example.com, a Next.js app.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Review" })).toHaveFocus();
    const summary = screen.getByRole("region", { name: "What the export defines" });
    expect(within(summary).getByText("1 job")).toBeInTheDocument();
    expect(within(summary).getByText("Schedule daily")).toBeInTheDocument();
    expect(within(summary).getByText("store.example.com")).toBeInTheDocument();
    expect(within(summary).getByText("2 values left out")).toBeInTheDocument();
    expect(screen.getByLabelText(/^STRIPE_KEY/)).toHaveAttribute("type", "password");
    await expectNoAxeViolations(harness.container);

    // The source had its credentials taken out and the secrets are empty: nothing goes yet.
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(/took the credentials out of this URL\. Enter it with them/)).toBeInTheDocument();
    expect(screen.getAllByText("The export left this value out. Enter the value the app had.")).toHaveLength(2);

    await user.clear(screen.getByLabelText("Domain"));
    await user.type(screen.getByLabelText("Domain"), "shop.example.org");
    await user.clear(screen.getByLabelText("Source"));
    await user.type(screen.getByLabelText("Source"), "https://github.com/acme/shop.git");
    await user.type(screen.getByLabelText(/^STRIPE_KEY/), "sk_live_1");
    await user.type(screen.getByLabelText(/^DATABASE_URL/), "postgres://db/shop");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toHaveFocus();
    expect(screen.getByText(/1 other name, 1 cron job,? and the backup schedule/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Import shop.example.org" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/import")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps/import")[0]?.body).toEqual({
      document: EXPORT,
      domain: "shop.example.org",
      source: "https://github.com/acme/shop.git",
      env: { DATABASE_URL: "postgres://db/shop", STRIPE_KEY: "sk_live_1" },
    });

    expect(await screen.findByText("shop.example.org is imported", {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(screen.getByText("2 of 3 parts of the export were applied.")).toBeInTheDocument();
    const notApplied = screen.getByRole("region", { name: "Not applied" });
    expect(within(notApplied).getByText("cron nightly")).toBeInTheDocument();
    expect(within(notApplied).getByText("The cron job needs a user that does not exist here")).toBeInTheDocument();
    const applied = screen.getByRole("region", { name: "Applied" });
    expect(within(applied).getByText("alias store.example.org")).toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("sends a domain the server refuses back to its field", { timeout: 20_000 }, async () => {
    const { harness } = wizard("shop.example.org", {}, {
      "POST /api/apps/import": () => problem(409, "domainconflicterror", "An application is already deployed on shop.example.org"),
    });
    const { user } = harness;
    await choose(user, "Import an application");
    await user.upload(screen.getByLabelText("Export file"), file(JSON.stringify({ ...EXPORT, env: {}, app: { ...EXPORT.app, source: "https://github.com/acme/shop.git" } })));
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    await user.clear(await screen.findByLabelText("Domain"));
    await user.type(screen.getByLabelText("Domain"), "shop.example.org");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Import shop.example.org" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Review" })).toBeInTheDocument();
    expect(screen.getByText("An application is already deployed on shop.example.org")).toBeInTheDocument();
  });
});

describe("another platform's configuration", () => {
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
    platform_proposal: {
      platform: "railway",
      files: ["railway.toml"],
      app_type: null,
      install_command: null,
      build_command: "npm run build",
      start_command: "node server.js",
      output_directory: null,
      port: 8080,
      health_path: "/healthz",
      health_timeout: 60,
      env: [{ name: "SESSION_SECRET", value: null, secret: true, generated: true, required: false, note: null }],
      databases: [],
      domains: [],
      persistent_paths: ["data"],
      warnings: ["Railway's cron schedule has no equivalent in WASM; add it with wasm cron add."],
    },
  };

  it("pre-fills what WASM supports, shows its commands for reference and its warnings verbatim, and can be turned off", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizard("api.example.com", {}, { "POST /api/apps/inspect": () => json(200, INSPECTION) });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "/srv/api");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Review" });

    const panel = screen.getByRole("region", { name: "Found a Railway configuration (railway.toml)" });
    expect(within(panel).getByText("node server.js")).toBeInTheDocument();
    expect(within(panel).getByText(/For reference only/)).toBeInTheDocument();
    expect(within(panel).getByText("GET /healthz, waiting up to 60 s")).toBeInTheDocument();
    expect(within(panel).getByText("Railway's cron schedule has no equivalent in WASM; add it with wasm cron add.")).toBeInTheDocument();
    expect(screen.getByLabelText("Port")).toHaveValue("8080");
    expect(screen.getByLabelText(/^SESSION_SECRET/)).toHaveAttribute("type", "password");
    expect(screen.getByLabelText<HTMLInputElement>(/^SESSION_SECRET/).value).toMatch(/^[A-Za-z0-9_-]{43}$/);
    await expectNoAxeViolations(harness.container);

    await user.click(within(panel).getByRole("checkbox", { name: "Use what it proposes" }));
    // shop.example.com holds 3000 in the fake machine: the detected default moves to 3001.
    expect(screen.getByLabelText("Port")).toHaveValue("3001");
    expect(screen.queryByLabelText(/^SESSION_SECRET/)).not.toBeInTheDocument();

    await user.click(within(panel).getByRole("checkbox", { name: "Use what it proposes" }));
    await user.type(screen.getByLabelText("Domain"), "api.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy api.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({
      port: 8080,
      persistent_paths: ["data"],
      app_type: "nodejs",
      health_path: "/healthz",
      health_timeout: 60,
    });
  });

  it("shows the server's refusal of its health check next to it, and takes it back when turned off", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizard("api.example.com", {}, {
      "POST /api/apps/inspect": () => json(200, INSPECTION),
      "POST /api/apps": () =>
        problem(400, "validationerror", "Health path must start with /", { fields: { health_path: "Health path must start with /" } }),
    });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "/srv/api");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Review" });
    await user.type(screen.getByLabelText("Domain"), "api.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy api.example.com" }));

    await screen.findByRole("heading", { level: 2, name: "Review" });
    const panel = screen.getByRole("region", { name: "Found a Railway configuration (railway.toml)" });
    expect(within(panel).getByRole("alert")).toHaveTextContent(/The server refused this health check/);
    expect(within(panel).getByText("Health path must start with /")).toBeInTheDocument();
    await expectNoAxeViolations(harness.container);

    await user.click(within(panel).getByRole("checkbox", { name: "Use what it proposes" }));
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy api.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(2);
    });
    expect(backend.callsTo("POST /api/apps")[1]?.body).not.toHaveProperty("health_path");
  });
});
