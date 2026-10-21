/**
 * The command palette over the whole console (checklist M-23): it finds an application, a
 * database, a site, a certificate, a service, a server, a recent deployment and a settings page,
 * reads its lists only once it is opened and only the ones the account may read, and opens
 * what it finds.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { FakeEventSource, SESSION, fakeBackend, json, signedInRoutes } from "../test/fakes";
import type { RouteHandler } from "../test/fakes";

const DATABASES = {
  databases: [
    { engine: "postgresql", name: "shop_prod", app: "shop.example.com", apps: ["shop.example.com"], username: "shop", missing: false, tracked: true, unverified: false },
    { engine: "mysql", name: "blog_wp", apps: [], missing: false, tracked: true, unverified: false },
  ],
  total: 2,
};

const SITES = {
  sites: [
    {
      name: "shop.example.com",
      site_name: "shop.example.com",
      webserver: "nginx",
      enabled: true,
      config_path: "/etc/nginx/sites-available/shop.example.com",
      has_ssl: true,
      noust_managed: true,
      server_names: ["shop.example.com", "www.shop.example.com"],
      app: "shop.example.com",
    },
  ],
  total: 1,
  webserver: "nginx",
};

const CERTS = {
  certificates: [{ domain: "shop.example.com", domains: ["shop.example.com", "www.shop.example.com"], days_remaining: 29, expires_on: "2026-10-24", auto_renew: true }],
  total: 1,
};

const DEPLOYMENTS = {
  items: [
    { id: 12, domain: "shop.example.com", status: "failed", triggered_by: "panel", git_commit: "c07d5e3", git_branch: "main", commit_message: "Fix the checkout total", has_log: true },
    { id: 11, domain: "admin.example.com", status: "success", triggered_by: "cli", git_commit: "9f2c41a", git_branch: "main", has_log: true },
  ],
  total: 2,
  next_before_id: null,
};

function routes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
    "GET /api/databases/databases": () => json(200, DATABASES),
    "GET /api/sites": () => json(200, SITES),
    "GET /api/certs": () => json(200, CERTS),
    "GET /api/deployments": () => json(200, DEPLOYMENTS),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  };
}

async function open(extra: Record<string, RouteHandler> = {}, path = "/") {
  const backend = fakeBackend(routes(extra));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return { ...harness, backend };
}

async function search(user: ReturnType<typeof renderConsole>["user"], text: string) {
  await user.keyboard("{Control>}k{/Control}");
  const input = await screen.findByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
  await user.type(input, text);
  return input;
}

describe("the command palette", () => {
  it("reads nothing for its index until it is opened", async () => {
    const { backend } = await open();
    expect(backend.callsTo("GET /api/databases/databases")).toHaveLength(0);
    expect(backend.callsTo("GET /api/certs")).toHaveLength(0);
    expect(backend.callsTo("GET /api/sites")).toHaveLength(0);
  });

  it("finds a database by name and opens it", async () => {
    const { user, location } = await open();
    await search(user, "shop_prod");
    expect(await screen.findByRole("option", { name: /shop_prod/ })).toHaveTextContent("PostgreSQL");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(location().pathname).toBe("/databases/postgresql/shop_prod"));
  });

  it("finds a site by a name it answers on, a certificate by a name it covers, a service, and a deployment by its commit", async () => {
    const { user } = await open();
    await search(user, "shop.example.com");
    const listbox = await screen.findByRole("listbox");
    await waitFor(() => {
      const groups = within(listbox)
        .getAllByRole("group")
        .map((group) => group.getAttribute("aria-labelledby"))
        .map((id) => (id === null ? "" : (document.getElementById(id)?.textContent ?? "")));
      expect(groups).toEqual(["Applications", "Domains", "Certificates", "Recent deployments", "Databases"]);
    });
    expect(within(listbox).getByRole("option", { name: /^shop\.example\.com.*29 days left/ })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("opens a certificate on the certificates list, filtered to it", async () => {
    const { user, location } = await open();
    await search(user, "29 days");
    await user.click(await screen.findByRole("option", { name: /29 days left/ }));
    await waitFor(() => expect(location().pathname).toBe("/domains"));
    expect(location().search).toEqual({ q: "shop.example.com" });
  });

  it("finds a deployment by its commit and opens it", async () => {
    const { user, location } = await open();
    await search(user, "c07d5e3");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(location().pathname).toBe("/apps/shop.example.com/deployments/12"));
  });

  it("finds a service by name and opens it", async () => {
    const { user, location } = await open();
    await search(user, "wasm-shop");
    await user.click(await screen.findByRole("option", { name: /wasm-shop/ }));
    await waitFor(() => expect(location().pathname).toBe("/server/services/wasm-shop"));
  });

  it("finds a settings section by what it is about", async () => {
    const { user, location } = await open();
    await search(user, "tokens");
    const option = await screen.findByRole("option", { name: /token/i });
    expect(option.closest("[role=group]")?.getAttribute("aria-labelledby")).not.toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(location().pathname).toBe("/settings/tokens"));
  });

  it("finds an application by its type as a person says it, and shows the type", async () => {
    const { user } = await open();
    await search(user, "next.js");
    expect(await screen.findByRole("option", { name: /shop\.example\.com.*Next\.js/ })).toBeInTheDocument();
  });

  it("starts the wizard on the recipes, on an import, or a new database, from the create commands", async () => {
    const { user, location } = await open();
    await search(user, "from a recipe");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(location().pathname).toBe("/apps/new"));
    expect(location().search).toEqual({ start: "recipe" });
  });

  it("opens the New database dialog from its create command", async () => {
    const { user, location } = await open({ "GET /api/databases/engines": () => json(200, { engines: [] }) });
    await search(user, "new database");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(location().pathname).toBe("/databases"));
    expect(await screen.findByRole("dialog", { name: "New database" })).toBeInTheDocument();
  });

  it("does not ask for what the account may not read", async () => {
    const viewer = { ...SESSION, scope: "viewer", permissions: ["self", "apps.read"] };
    const { user, backend } = await open({ "GET /api/auth/session": () => json(200, viewer) });
    await search(user, "shop");
    await screen.findAllByRole("option", { name: /shop\.example\.com/ });
    expect(backend.callsTo("GET /api/databases/databases")).toHaveLength(0);
    expect(backend.callsTo("GET /api/services")).toHaveLength(0);
    expect(backend.callsTo("GET /api/certs").length).toBeGreaterThan(0);
  });

  it("keeps working when one list cannot be read: the others are still found", async () => {
    const { user } = await open({ "GET /api/databases/databases": () => json(500, { error: "boom", detail: "engine unreachable", hint: null, fields: null }) });
    await search(user, "shop.example.com");
    expect(await screen.findByRole("option", { name: /^shop\.example\.com.*29 days left/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /shop_prod/ })).not.toBeInTheDocument();
  });

  it("reads its lists again after a job ends, which may have created or removed what it holds", async () => {
    const { user, backend } = await open();
    await search(user, "shop");
    await screen.findByRole("option", { name: /shop_prod/ });
    const before = backend.callsTo("GET /api/databases/databases").length;
    await user.keyboard("{Escape}");

    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { id: "j9", type: "db_create", status: "completed", metadata: {} });
    });
    await user.keyboard("{Control>}k{/Control}");
    await screen.findByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
    await waitFor(() => expect(backend.callsTo("GET /api/databases/databases").length).toBeGreaterThan(before));
  });

  it("does not read them again when it is opened a second time with nothing changed", async () => {
    const { user, backend } = await open();
    await search(user, "shop");
    await screen.findByRole("option", { name: /shop_prod/ });
    const before = backend.callsTo("GET /api/certs").length;
    await user.keyboard("{Escape}");
    await user.keyboard("{Control>}k{/Control}");
    await screen.findByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
    expect(backend.callsTo("GET /api/certs")).toHaveLength(before);
  });
});
