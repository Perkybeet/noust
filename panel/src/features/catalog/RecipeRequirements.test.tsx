import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { catalogueRoutes } from "./testFixtures";

/** The log viewer sizes its window from the layout, which jsdom does not have. */
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
});

const READY = {
  recipe: "vaultwarden",
  ready: true,
  items: [{ kind: "tool", name: "Docker with the Compose plugin", met: true, detail: "Docker 27.3 with Compose v2.29", fix: null, installable: false }],
};

const MISSING = {
  recipe: "vaultwarden",
  ready: false,
  items: [
    { kind: "tool", name: "Docker with the Compose plugin", met: true, detail: "Docker 27.3 with Compose v2.29", fix: null, installable: false },
    { kind: "runtime", name: "node@22", met: false, detail: "Node.js 22 is not installed", fix: "noust runtime install node 22", installable: true },
    { kind: "service", name: "postgresql", met: false, detail: "PostgreSQL is not installed", fix: "noust db install postgresql", installable: false },
  ],
};

/** The element at an index of a list a test has just shown to be long enough. */
function nth(items: readonly HTMLElement[], index: number): HTMLElement {
  const item = items[index];
  if (item === undefined) throw new Error(`no element at ${String(index)}`);
  return item;
}

const APP_TYPES = { types: [{ type: "docker-compose", name: "Docker Compose", default_port: 0 }] };

function console_(path: string, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...catalogueRoutes(),
    "POST /api/apps": (call) =>
      json(202, {
        job_id: "5a1c0de0",
        status: "pending",
        message: "Queued",
        job: { id: "5a1c0de0", type: "deploy", name: "Deploy", description: "", status: "running", progress: 10, total_steps: 100, current_step: "", created_at: "2026-10-06T10:21:13", deployment_id: null, logs: [], metadata: { domain: (call.body as { domain: string }).domain } },
      }),
    ...extra,
  });
  return { backend, harness: renderConsole(path) };
}

describe("what a recipe needs from the server", { timeout: 40_000 }, () => {
  it("is listed on the recipe's page with what was found and the command that provides it", async () => {
    console_("/catalog/vaultwarden", { "GET /api/recipes/vaultwarden/requirements": () => json(200, MISSING) });

    const list = await screen.findByRole("list", { name: "What this server has" }, { timeout: 15_000 });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(within(nth(items, 0)).getByText("Ready")).toBeInTheDocument();
    expect(within(nth(items, 1)).getByText("Missing")).toBeInTheDocument();
    expect(within(nth(items, 1)).getByText("Noust can install it")).toBeInTheDocument();
    expect(within(nth(items, 1)).getByText("Node.js 22 is not installed")).toBeInTheDocument();
    expect(within(nth(items, 1)).getByText("noust runtime install node 22")).toBeInTheDocument();
    expect(within(nth(items, 2)).queryByText("Noust can install it")).not.toBeInTheDocument();
    expect(screen.getByText("2 things are missing on this server.")).toBeInTheDocument();
    // On the page it reports; it offers nothing to tick.
    expect(screen.queryByRole("checkbox", { name: /Install what is missing first/ })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says everything is there when it is", async () => {
    console_("/catalog/vaultwarden", { "GET /api/recipes/vaultwarden/requirements": () => json(200, READY) });

    expect(await screen.findByText("Everything this recipe needs is on this server.")).toBeInTheDocument();
  });

  it("is not shown by a server that cannot say", async () => {
    console_("/catalog/vaultwarden");

    await screen.findByRole("heading", { level: 2, name: "About" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "What this server has" })).not.toBeInTheDocument());
  });
});

describe("the install form of a recipe", { timeout: 40_000 }, () => {
  it("asks for the daily backups by default and sends the choice with the install", async () => {
    const { backend, harness } = console_("/apps/new?start=recipe&recipe=vaultwarden", { "GET /api/recipes/vaultwarden/requirements": () => json(200, READY) });
    const { user } = harness;

    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    const backups = screen.getByRole("checkbox", { name: /Back it up every day/ });
    expect(backups).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: /Install what is missing first/ })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Domain"), "vault.example.com");
    await user.click(backups);
    await user.click(screen.getByRole("button", { name: "Deploy" }));

    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ recipe: "vaultwarden", default_backups: false, install_requirements: false });
  });

  it("offers to install what the server lacks, and sends it when ticked", async () => {
    const { backend, harness } = console_("/apps/new?start=recipe&recipe=vaultwarden", { "GET /api/recipes/vaultwarden/requirements": () => json(200, MISSING) });
    const { user } = harness;

    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    const install = await screen.findByRole("checkbox", { name: /Install what is missing first/ });
    expect(install).not.toBeChecked();
    expect(screen.getByText(/Something is missing that Noust cannot install for you/)).toBeInTheDocument();
    await user.click(install);
    await user.type(screen.getByLabelText("Domain"), "vault.example.com");
    await user.click(screen.getByRole("button", { name: "Deploy" }));

    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ default_backups: true, install_requirements: true });
  });
});
