import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import type { Inspection } from "./wizard";

const INSPECTION: Inspection = {
  app_type: "nextjs",
  detected_types: ["nextjs", "nodejs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  default_port: 3000,
  env_keys: [
    { name: "DATABASE_URL", default: null, secret: false, required: true },
    { name: "NEXTAUTH_SECRET", default: null, secret: true, required: true },
    { name: "LOG_LEVEL", default: "info", secret: false, required: false },
  ],
  branch: "",
  commit: "",
};

/** Every type `GET /api/apps/types` lists, in the API's own order (alphabetical, auto last). */
const APP_TYPES = {
  types: [
    { type: "docker-compose", name: "Docker Compose", default_port: 3000 },
    { type: "monorepo", name: "Monorepo (Turborepo/pnpm)", default_port: 3000 },
    { type: "nextjs", name: "Next.js", default_port: 3000 },
    { type: "nodejs", name: "Node.js", default_port: 3000 },
    { type: "python", name: "Python (Django/Flask/FastAPI)", default_port: 8000 },
    { type: "static", name: "Static Site", default_port: 80 },
    { type: "vite", name: "Vite (React/Vue/Svelte)", default_port: 5173 },
    { type: "auto", name: "Auto-detect", default_port: 3000 },
  ],
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

/** `GET /api/domains/dns`: everything under `elsewhere.` resolves to another server. */
function dnsRoute(): RouteHandler {
  return (call) => {
    const name = call.search.get("name") ?? "";
    const here = !name.startsWith("elsewhere.");
    return json(200, {
      domain: name,
      expected_addresses: ["203.0.113.10"],
      resolved_addresses: here ? ["203.0.113.10"] : ["198.51.100.23"],
      points_here: here,
    });
  };
}

function wizard(extra: Record<string, RouteHandler> = {}) {
  let deployed = false;
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": dnsRoute(),
    "POST /api/apps/inspect": () => json(200, INSPECTION),
    "POST /api/apps": () => {
      deployed = true;
      return json(202, { job_id: JOB.id, status: "pending", message: "Deployment queued", job: JOB });
    },
    // The deployer writes the deployment row (with this job's id) well before the job itself
    // ends, which is what the wizard actually waits for.
    "GET /api/deployments": () =>
      json(200, {
        items: deployed ? [{ id: 7, domain: "storefront.example.com", status: "running", triggered_by: "panel", has_log: true, job_id: JOB.id }] : [],
        total: deployed ? 1 : 0,
        next_before_id: null,
      }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [JOB], total: 1, active: 1 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

async function inspect(user: ReturnType<typeof renderConsole>["user"], source = "/var/www/src/storefront") {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.type(screen.getByLabelText("Repository or directory"), source);
  await user.click(screen.getByRole("button", { name: "Inspect source" }));
  return screen.findByRole("heading", { level: 2, name: "Address" });
}

/** From the Address step to Variables, through Configuration, with the domain typed. */
async function toVariables(user: ReturnType<typeof renderConsole>["user"], domain: string) {
  await user.type(screen.getByLabelText("Domain"), domain);
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { level: 2, name: "Configuration" });
  await user.click(screen.getByRole("button", { name: "Continue" }));
  return screen.findByRole("heading", { level: 2, name: "Variables" });
}

/** The required variables of the fixture, filled in. */
async function fillRequired(user: ReturnType<typeof renderConsole>["user"]) {
  await user.type(screen.getByLabelText(/^DATABASE_URL/), "x");
  await user.type(screen.getByLabelText(/^NEXTAUTH_SECRET/), "y");
}

describe("the new-app wizard", () => {
  it("inspects the source, proposes what it found, and hands over to the deployment once it starts", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    const heading = await inspect(user);
    expect(heading).toHaveFocus();
    expect(backend.callsTo("POST /api/apps/inspect")[0]?.body).toEqual({ source: "/var/www/src/storefront" });
    // The stepper says where the operator is, in words.
    const steps = screen.getByRole("list", { name: "Steps" });
    expect(within(steps).getByText("Source: done")).toBeInTheDocument();
    expect(within(steps).getByText("Address: current step")).toBeInTheDocument();
    // The source is not asked again.
    expect(screen.queryByLabelText("Repository or directory")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Domain"), "storefront.example.com");
    // Where it points, checked as it is typed.
    await screen.findByText("storefront.example.com points here", {}, { timeout: 2_000 });
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // What was found, shown as it will run; the type is a choice, its labels from GET /api/apps/types.
    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toHaveFocus();
    expect(screen.getByText("npm ci")).toBeInTheDocument();
    expect(screen.getByText("npm run build")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "App type" })).toHaveTextContent("Next.js");
    // shop.example.com holds 3000 in the fake machine, so the next free port is proposed.
    expect(screen.getByLabelText("Port")).toHaveValue("3001");
    // What most deploys leave alone is folded, and says what it is set to.
    expect(screen.getByText("Advanced").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Instant rollback, nginx")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { level: 2, name: "Variables" });
    await user.type(screen.getByLabelText(/^DATABASE_URL/), "postgres://db/storefront");
    const secret = screen.getByLabelText(/^NEXTAUTH_SECRET/);
    expect(secret).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Generate NEXTAUTH_SECRET" }));
    expect((secret as HTMLInputElement).value).toMatch(/^[A-Za-z0-9_-]{43}$/);
    // A variable that already has a value is folded under one line.
    expect(screen.getByText("1 more variable, with a value already")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toHaveFocus();
    expect(screen.getByText("https://storefront.example.com")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Deploy storefront.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toEqual({
      domain: "storefront.example.com",
      source: "/var/www/src/storefront",
      app_type: "nextjs",
      port: 3001,
      webserver: "nginx",
      ssl: true,
      layout: "releases",
      include_www: false,
      memory_max_mb: null,
      cpu_quota_percent: null,
      tasks_max: null,
      env_vars: {
        DATABASE_URL: "postgres://db/storefront",
        NEXTAUTH_SECRET: (secret as HTMLInputElement).value,
        LOG_LEVEL: "info",
      },
      skip_database: false,
    });
    // The wizard hands over once the deployments list carries a row for this exact job, not
    // once some heuristic guesses which row is this one's.
    await waitFor(() => {
      expect(harness.location().pathname).toBe("/apps/storefront.example.com/deployments/7");
    });
  });

  it("never disables Continue: it says what is missing, and takes the operator to it", async () => {
    const { harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    const next = screen.getByRole("button", { name: "Inspect source" });
    expect(next).toBeEnabled();
    await user.click(next);
    expect(await screen.findAllByText("Enter a Git URL or a path on this server.")).toHaveLength(2);
    await waitFor(() => {
      expect(screen.getByLabelText("Repository or directory")).toHaveFocus();
    });

    await user.type(screen.getByLabelText("Repository or directory"), "/var/www/src/storefront");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    await user.type(screen.getByLabelText("Domain"), "shop.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    // A domain already deployed here: said on the field and by Continue, and the field takes focus.
    expect(await screen.findAllByText(/shop\.example\.com is already deployed/)).toHaveLength(2);
    await waitFor(() => {
      expect(screen.getByLabelText("Domain")).toHaveFocus();
    });
    expect(screen.getByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
  });

  it("keeps the required variables from being skipped", async () => {
    const { harness } = wizard();
    const { user } = harness;
    await inspect(user);
    await toVariables(user, "storefront.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    // Each field says why; the bar counts them and names the first.
    expect(await screen.findAllByText(".env.example gives it no value, so the app expects one.")).toHaveLength(2);
    expect(screen.getByText("2 fields need a fix before you continue, starting with DATABASE_URL.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Variables" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText(/^DATABASE_URL/)).toHaveFocus();
    });
  });

  it("sends a domain the server refuses back to its field on the Address step", async () => {
    const { harness } = wizard({
      "POST /api/apps": () => problem(409, "conflict", "Application already exists: storefront.example.com"),
    });
    const { user } = harness;
    await inspect(user);
    await toVariables(user, "storefront.example.com");
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy storefront.example.com" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
    expect(screen.getByText("Application already exists: storefront.example.com")).toBeInTheDocument();
  });

  it("shows why an inspection failed, and lets the type be chosen when none matched", async () => {
    const { harness } = wizard({
      "POST /api/apps/inspect": () =>
        problem(500, "deploymenterror", "Could not identify the application type at /srv/odd", {
          hint: "Nothing under the fetched source matches a registered application type.",
        }),
    });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "/srv/odd");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    const detail = await screen.findByText("Could not identify the application type at /srv/odd");
    expect(detail.closest("[role=alert]")).not.toBeNull();
    expect(screen.getByText("Nothing under the fetched source matches a registered application type.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Choose the type yourself" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    await user.type(screen.getByLabelText("Domain"), "odd.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    expect(screen.getByText(/No type recognised this source/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "App type" })).toHaveTextContent("Choose a type");
    // No type yet: Continue says so instead of moving on.
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findAllByText(/Choose the app type/)).not.toHaveLength(0);
    expect(screen.getByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
  });

  it("says what Noust found and that it can deploy it, in the console's own words", async () => {
    const { harness } = wizard({
      "POST /api/apps/inspect": () =>
        json(200, {
          ...INSPECTION,
          compatible: true,
          verdict: "Noust can deploy this as Next.js. It also looks like Node.js; choose that type instead to deploy it that way.",
          suggestion: null,
        }),
    });
    await inspect(harness.user);
    await harness.user.type(screen.getByLabelText("Domain"), "storefront.example.com");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));
    const found = await screen.findByRole("group", { name: "What Noust found" });
    // A deployable source is said from the catalogs, not in the backend's English prose.
    expect(within(found).getByText("Noust can deploy this as Next.js.")).toBeInTheDocument();
    expect(within(found).queryByText(/It also looks like Node\.js/)).not.toBeInTheDocument();
    expect(within(found).getByText(/It also matches Node\.js\./)).toBeInTheDocument();
  });

  it("warns on the review step when this server cannot deploy it as it is, with what to do first", async () => {
    const { harness } = wizard({
      "POST /api/apps/inspect": () =>
        json(200, {
          ...INSPECTION,
          app_type: "python",
          detected_types: ["python"],
          compatible: false,
          verdict: "This is a Python (Django/Flask/FastAPI) project, but this server does not have python3.",
          suggestion: "Install what it needs with `noust setup init`, then deploy it.",
        }),
    });
    await inspect(harness.user);
    await harness.user.type(screen.getByLabelText("Domain"), "storefront.example.com");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));
    const found = await screen.findByRole("group", { name: "What Noust found" });
    expect(within(found).getByText(/this server does not have python3/)).toBeInTheDocument();
    expect(within(found).getByText("Not deployable as it is:", { exact: false })).toBeInTheDocument();
    expect(within(found).getByText("noust setup init")).toHaveAttribute("translate", "no");
    await expectNoAxeViolations(found);
  });

  it("shows the inspection's verdict on a source it cannot deploy, and the file to add as one, verbatim", async () => {
    const compose = "services:\n  app:\n    build: .\n    ports:\n      - \"3000:3000\"";
    const { harness } = wizard({
      "POST /api/apps/inspect": () =>
        problem(400, "validationerror", "The repository has a Dockerfile but no Compose file.", {
          hint: `Noust runs containers through Docker Compose. Commit a compose.yaml next to the Dockerfile that builds it:\n\n${compose}\n\nwith the port the image listens on, then deploy it as Docker Compose.`,
        }),
    });
    const { user, container } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "https://github.com/acme/api.git");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    const detail = await screen.findByText("The repository has a Dockerfile but no Compose file.");
    expect(detail.closest("[role=status]")).not.toBeNull();
    expect(screen.getByText("Noust cannot deploy https://github.com/acme/api.git as it is")).toBeInTheDocument();
    // The compose file keeps its indentation: it is shown as the file it is.
    const file = screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent === compose);
    expect(file).toBeInTheDocument();
    expect(screen.getByText(/with the port the image listens on/)).toBeInTheDocument();
    // Not a fetch failure: the source field is not marked.
    expect(screen.getByLabelText("Repository or directory")).not.toHaveAttribute("aria-invalid", "true");
    await expectNoAxeViolations(container);
    await user.click(screen.getByRole("button", { name: "Choose the type yourself" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
  });

  it("says honestly what it is doing while it inspects, and Cancel stops the request", async () => {
    const { harness } = wizard();
    let aborted = false;
    const inner = globalThis.fetch;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init: RequestInit = {}) => {
      const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (!href.endsWith("/api/apps/inspect")) return inner(input, init);
      // Answers nothing until the console gives up on it, as a slow clone would.
      return new Promise<Response>((_, reject) => {
        init.signal?.addEventListener("abort", () => {
          aborted = true;
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      });
    });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "https://github.com/acme/shop.git");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    const reading = await screen.findByText("Reading the repository…");
    expect(reading.closest("[role=status]")).not.toBeNull();
    expect(screen.getByText(/Cancel stops it on the server too/)).toBeInTheDocument();
    // What the inspection does, said as a list; no part of it claims to be done or in progress.
    const phases = screen.getByRole("list", { name: "What the inspection does" });
    expect(within(phases).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByText(/Cloning|Detecting|Installing/)).not.toBeInTheDocument();

    // The wizard's own Continue is busy while it reads; Cancel stops it.
    expect(screen.getByRole("button", { name: "Inspect source" })).toHaveAttribute("aria-busy", "true");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(aborted).toBe(true);
    await waitFor(() => {
      expect(screen.queryByText("Reading the repository…")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Inspect source" })).toBeInTheDocument();
    // Cancelling is not a failure.
    expect(screen.queryByText(/Could not inspect/)).not.toBeInTheDocument();
  });

  it("shows the fetch failure on the source field, verbatim, with its hint above it", async () => {
    const { harness } = wizard({
      "POST /api/apps/inspect": () =>
        problem(400, "sourceerror", "Download failed: https://example.com/app.tar.gz", { hint: "Name or service not known" }),
    });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "https://example.com/app.tar.gz");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    expect(await screen.findByText("Download failed: https://example.com/app.tar.gz")).toBeInTheDocument();
    expect(screen.getByLabelText("Repository or directory")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Name or service not known")).toBeInTheDocument();
  });

  it("shows git's own output verbatim for a private repository, with the backend's real fix", async () => {
    const { harness } = wizard({
      "POST /api/apps/inspect": () =>
        problem(400, "sourceerror", "Could not access git@github.com:acme/storefront.git", {
          hint: "This looks like a private repository. Add a deploy key, or embed a token in the URL.",
          output: "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.",
        }),
    });
    const { user, container } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "git@github.com:acme/storefront.git");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    // The short sentence is on the field, where the operator is already looking.
    expect(await screen.findByText("Could not access git@github.com:acme/storefront.git")).toBeInTheDocument();
    // The block below carries git's own report and the backend's actionable fix, verbatim.
    expect(screen.getByText("This looks like a private repository. Add a deploy key, or embed a token in the URL.")).toBeInTheDocument();
    expect(screen.getByText(/Permission denied \(publickey\)/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("offers www and resource limits, and sends them in the deploy request", async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await inspect(user);
    await user.type(screen.getByLabelText("Domain"), "newapp.io");
    await user.click(screen.getByRole("checkbox", { name: "Also serve www" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Configuration" });

    await user.click(screen.getByText("Advanced"));
    // Scoped: the topbar's machine strip has its own "Memory" and "CPU" meters.
    const limits = within(screen.getByRole("group", { name: "Resource limits" }));
    await user.type(limits.getByLabelText(/^Memory/), "512");
    await user.type(limits.getByLabelText(/^CPU/), "50");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Variables" });
    await fillRequired(user);

    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy newapp.io" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({
      domain: "newapp.io",
      include_www: true,
      memory_max_mb: 512,
      cpu_quota_percent: 50,
      tasks_max: null,
    });
  });

  it("warns, without blocking, when the typed domain resolves elsewhere", async () => {
    const { harness } = wizard();
    const { user } = harness;
    await inspect(user);
    await user.type(screen.getByLabelText("Domain"), "elsewhere.example.com");
    await screen.findByText("elsewhere.example.com points somewhere else", {}, { timeout: 2_000 });
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
  });
});
