/**
 * The first deploy of a server: a pasted source is read at once and says what it found, a server
 * with no domain of its own answers on a temporary address without being asked for one, and the
 * summary is what a person wants to know, with how Noust does it behind "Details".
 * `e2e/first-run.spec.ts` walks the same road in a browser against a server with nothing on it.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { RECIPES, WORDPRESS } from "./testFixtures";
import type { Inspection } from "./wizard";

const NEXT: Inspection = {
  app_type: "nextjs",
  detected_types: ["nextjs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  default_port: 3000,
  env_keys: [
    { name: "DATABASE_URL", default: null, secret: false, required: true },
    { name: "API_TOKEN", default: null, secret: true, required: true },
  ],
  branch: "main",
  commit: "a1b2c3d",
  compatible: true,
};

const APP_TYPES = {
  types: [
    { type: "nextjs", name: "Next.js", default_port: 3000 },
    { type: "nodejs", name: "Node.js", default_port: 3000 },
  ],
};

const JOB = {
  id: "5a1c0de0",
  type: "deploy",
  name: "Deploy",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Downloading the source",
  created_at: "2026-10-20T10:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "api.203-0-113-10.sslip.io" },
};

const TEMPORARY = "api.203-0-113-10.sslip.io";

/** A server with nothing on it, whose address is known, unless the test says otherwise. */
function emptyServer(inspection: Inspection, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, { total: 0, apps: [] }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/temporary": () => json(200, { domain: TEMPORARY, address: "203.0.113.10" }),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "POST /api/apps/inspect": () => json(200, inspection),
    "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Queued", job: JOB }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

describe("a pasted source", () => {
  it("is read without a button and says what it found, with Change to open it", { timeout: 30_000 }, async () => {
    const { backend, harness } = emptyServer(NEXT);
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    // The way it used to be asked for is gone.
    expect(screen.queryByRole("button", { name: "Inspect source" })).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("https://github.com/acme/api.git");

    const found = await screen.findByText("Detected: Next.js with npm. Port 3000. 2 variables to fill in.", {}, { timeout: 5_000 });
    expect(found.closest("[role=status]")).not.toBeNull();
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("does not read a path on this server until Continue is pressed", { timeout: 30_000 }, async () => {
    const { backend, harness } = emptyServer(NEXT);
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("/srv/apps/api");
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
    });
  });
});

describe("the first application of a server without a domain", () => {
  it("is answered on a temporary address: Continue lands on the variables, then the deploy, never the address", { timeout: 40_000 }, async () => {
    const { backend, harness } = emptyServer(NEXT);
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("https://github.com/acme/api.git");
    await screen.findByText(/Detected: Next\.js with npm/);

    await user.click(screen.getByRole("button", { name: "Continue" }));
    // Two variables have no value: that is what is left to ask, and the address is not.
    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Steps" });
    expect(within(steps).queryByText("Address")).not.toBeInTheDocument();
    expect(within(steps).queryByText("Configuration")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/^DATABASE_URL/), "postgres://db/app");
    await user.type(screen.getByLabelText(/^API_TOKEN/), "s3cret");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    // The summary says what a person wants to know; how Noust does it waits behind Details.
    expect(screen.getByText(`https://${TEMPORARY}`)).toBeInTheDocument();
    expect(screen.getByText(/A temporary address is for trying things out/)).toBeInTheDocument();
    expect(screen.getByText("Web server")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("Web server")).toBeVisible();
    expect(screen.getByText("https://github.com/acme/api.git")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: `Deploy ${TEMPORARY}` }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ domain: TEMPORARY, ssl: true, include_www: false });
  });

  it("opens the address from the summary, offers the choice, and a domain of its own brings its DNS check", { timeout: 40_000 }, async () => {
    const { backend, harness } = emptyServer({ ...NEXT, env_keys: [] });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("https://github.com/acme/api.git");
    await screen.findByText(/Detected: Next\.js with npm/);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Deploy" });

    await user.click(screen.getByRole("button", { name: "Change address" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Temporary address/ })).toBeChecked();
    expect(screen.queryByLabelText("Domain")).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("radio", { name: /My own domain/ }));
    await user.type(await screen.findByLabelText("Domain"), "blog.example.com");
    // One line says whether it points here.
    expect(await screen.findByText("blog.example.com points here")).toBeInTheDocument();
    expect(screen.queryByText("Show the records")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy blog.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ domain: "blog.example.com" });
  });

  it("asks for the address as before where a server cannot make a temporary one", { timeout: 40_000 }, async () => {
    const { harness } = emptyServer(
      { ...NEXT, env_keys: [] },
      {
        "GET /api/domains/temporary": () => json(409, { error: "no_public_address", message: "This server's public address is not known", hint: "Declare it" }),
      },
    );
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("https://github.com/acme/api.git");
    await screen.findByText(/Detected: Next\.js with npm/);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
    expect(screen.getByLabelText("Domain")).toBeInTheDocument();
  });
});

describe("a private repository over SSH", () => {
  const KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJm3XHLbCqk8mP0nS0yv4Y1v8f0eJ3o0n7m0q8c1b2xP root@server";
  const REFUSED = "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.";

  function refusingServer(key: Record<string, unknown>) {
    let reads = 0;
    const made = emptyServer(NEXT, {
      "GET /api/deploy-keys/server": () => json(200, key),
      "POST /api/apps/inspect": () => {
        reads += 1;
        // The second read finds the key in place.
        return reads === 1 ? problem(400, "sourceerror", "Could not fetch git@github.com:acme/private.git", { output: REFUSED, hint: "Add this server's key to the repository." }) : json(200, NEXT);
      },
    });
    return made;
  }

  it("gives the server's key under git's own words, the page where it is added, and Check again", { timeout: 40_000 }, async () => {
    const { backend, harness } = refusingServer({ public_key: KEY, fingerprint: "SHA256:abc" });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("git@github.com:acme/private.git");

    // Git's words stay exactly as it printed them.
    expect(await screen.findByText(/Permission denied \(publickey\)/, {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(await screen.findByText("This server's SSH key")).toBeInTheDocument();
    expect(screen.getByText(KEY)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open the deploy keys on GitHub/ })).toHaveAttribute("href", "https://github.com/acme/private/settings/keys/new");
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(2);
    });
    expect(await screen.findByText(/Detected: Next\.js with npm/)).toBeInTheDocument();
    expect(screen.queryByText("This server's SSH key")).not.toBeInTheDocument();
  });

  it("says how to make the key where the server has none, and names no page for a host it does not know", { timeout: 40_000 }, async () => {
    const { harness } = refusingServer({ public_key: null, fingerprint: null });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("git@git.example.org:team/private.git");
    expect(await screen.findByText(/This server has no SSH key yet/, {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(screen.getByText("noust setup ssh --generate")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Open the deploy keys/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check again" })).toBeInTheDocument();
  });

  it("shows no key block for an address that is not SSH", { timeout: 40_000 }, async () => {
    const { harness } = emptyServer(NEXT, {
      "POST /api/apps/inspect": () => problem(400, "sourceerror", "Could not fetch https://github.com/acme/private.git", { output: "fatal: could not read Username", hint: "Use a token in the URL." }),
    });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.click(screen.getByLabelText("Repository or directory"));
    await user.paste("https://github.com/acme/private.git");
    expect(await screen.findByText(/could not read Username/, {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(screen.queryByText("This server's SSH key")).not.toBeInTheDocument();
  });
});

describe("Try a demo", () => {
  const HELLO = { ...WORDPRESS, name: "hello", title: "Hello", database: null, dependencies: [], requires: [], variables: [] };

  function demoServer(extra: Record<string, RouteHandler> = {}) {
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/apps": () => json(200, { total: 0, apps: [] }),
      "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
      "GET /api/apps/types": () => json(200, APP_TYPES),
      "GET /api/domains/temporary": (call) => json(200, { domain: `${call.search.get("prefix") ?? "app"}.203-0-113-10.sslip.io`, address: "203.0.113.10" }),
      "GET /api/recipes": () => json(200, RECIPES),
      "GET /api/recipes/hello": () => json(200, HELLO),
      "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Queued", job: JOB }),
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      ...extra,
    });
    return { backend, harness: renderConsole("/apps/new?start=recipe&recipe=hello&deploy=now") };
  }

  it("deploys the recipe on its temporary address without another press, once", { timeout: 30_000 }, async () => {
    const { backend, harness } = demoServer();
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ recipe: "hello", domain: "hello.203-0-113-10.sslip.io", ssl: true, include_www: false });
    // The link is spent: the address no longer asks for a deploy, so a reload does not repeat it.
    await waitFor(() => {
      expect(harness.location().search).not.toContain("deploy");
    });
  });

  it("stays on the form when the server cannot make a temporary address", { timeout: 30_000 }, async () => {
    const { backend } = demoServer({
      "GET /api/domains/temporary": () => json(409, { error: "no_public_address", message: "This server's public address is not known", hint: "Declare it" }),
    });
    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps")).toHaveLength(0);
  });
});

describe("the ways in", () => {
  it("keeps the common ones on the row and the rest behind More ways", { timeout: 30_000 }, async () => {
    const { harness } = emptyServer(NEXT);
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    const ways = screen.getByRole("radiogroup", { name: "Where the code is" });
    expect(within(ways).getByRole("radio", { name: "URL or path" })).toBeChecked();
    expect(within(ways).queryByRole("radio", { name: "Compose" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "More ways" }));
    for (const name of ["Compose", "Import an application", "Already running", "From a recipe"]) {
      expect(await screen.findByRole("menuitem", { name })).toBeInTheDocument();
    }
  });
});
