/**
 * Importing an application from a Coolify or Dokploy server (checklist N-13): the console asks for
 * the server and a read-only token, lists what the server has, reads the one chosen into the same
 * review an export file goes through, and keeps the token for the length of those two requests.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { EXPORT } from "./testFixtures";

const APP_TYPES = {
  types: [
    { type: "nextjs", name: "Next.js", default_port: 3000 },
    { type: "docker-compose", name: "Docker Compose", default_port: 3000 },
  ],
};

const APPLICATIONS = {
  platform: "coolify",
  applications: [
    { ref: "abc123", name: "shop", build: "nixpacks", source: "https://github.com/acme/shop", domains: ["shop.example.com"], project: "acme" },
    { ref: "def456", name: "worker", build: "dockerfile", source: "", domains: [], project: "acme" },
  ],
};

const DOCUMENT = {
  ...EXPORT,
  app: { ...EXPORT.app, domain: "store.example.net", source: "https://github.com/acme/shop.git" },
  env: { NODE_ENV: { secret: false, value: "production" } },
  env_secret_marks: {},
  domains: { aliases: [], redirects: [] },
  cron: [],
};

const PROPOSAL = {
  proposal: {
    platform: "coolify",
    files: [],
    app_type: "nextjs",
    install_command: "npm ci",
    build_command: "npm run build",
    start_command: "npm run start",
    port: 3000,
    env: [],
    databases: [],
    domains: ["shop.example.com"],
    persistent_paths: [],
    warnings: ["Coolify's preview deployments have no setting here: run noust previews enable to get them."],
    name: "shop",
    source: "https://github.com/acme/shop",
    watch_paths: [],
    cron: [],
    equivalent: null,
  },
  document: DOCUMENT,
  deployable: true,
};

function remote(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "POST /api/import/applications": () => json(200, APPLICATIONS),
    "POST /api/import/proposal": () => json(200, PROPOSAL),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

async function startFromPlatform(harness: ReturnType<typeof renderConsole>, platform = "Coolify") {
  const { user } = harness;
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.click(await screen.findByRole("radio", { name: "Import an application" }));
  await user.click(await screen.findByRole("radio", { name: platform }));
  return user;
}

async function readServer(user: ReturnType<typeof renderConsole>["user"], url = "https://coolify.acme.dev", token = "tok-secret-123") {
  await user.type(screen.getByLabelText("Server address"), url);
  await user.type(screen.getByLabelText("API token"), token);
  await user.click(screen.getByRole("button", { name: "Read applications" }));
}

describe("importing from a Coolify server", () => {
  it("lists the server's applications, reads the one chosen and goes on as an import", { timeout: 25_000 }, async () => {
    const { backend, harness } = remote();
    const user = await startFromPlatform(harness);
    expect(screen.getByLabelText("API token")).toHaveAttribute("type", "password");
    // Nothing is asked of the server until both fields say where and with what.
    expect(screen.getByRole("button", { name: "Read applications" })).toBeDisabled();

    await readServer(user);

    await waitFor(() => {
      expect(backend.callsTo("POST /api/import/applications")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/import/applications")[0]?.body).toEqual({
      platform: "coolify",
      url: "https://coolify.acme.dev",
      token: "tok-secret-123",
    });
    await user.click(await screen.findByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: /shop/ }));
    // The domain it answered on there is the one proposed here, to change if it should be another.
    const domain = screen.getByLabelText("Domain here");
    expect(domain).toHaveValue("shop.example.com");
    await user.clear(domain);
    await user.type(domain, "store.example.net");
    await user.click(screen.getByRole("button", { name: "Read the application" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/import/proposal")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/import/proposal")[0]?.body).toEqual({
      platform: "coolify",
      url: "https://coolify.acme.dev",
      token: "tok-secret-123",
      ref: "abc123",
      domain: "store.example.net",
    });
    expect(await screen.findByText(/^Read shop from Coolify/)).toBeInTheDocument();
    expect(screen.getByText(/Coolify's preview deployments have no setting here/)).toBeInTheDocument();
    // The token served its two requests: it is neither on the page nor in the form any more.
    expect(screen.queryByText(/tok-secret-123/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("API token")).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address" })).toHaveFocus();
    expect(screen.getByLabelText("Domain")).toHaveValue("store.example.net");
    expect(screen.getByLabelText("Source")).toHaveValue("https://github.com/acme/shop.git");
  });

  it("says what the other server answered, in its own words, and asks nothing more of it", { timeout: 20_000 }, async () => {
    const { backend, harness } = remote({
      "POST /api/import/applications": () => problem(502, "integrationerror", "Coolify answered 401 Unauthenticated: the token is missing the read scope"),
    });
    const user = await startFromPlatform(harness);

    await readServer(user);

    expect(await screen.findByText(/Coolify answered 401 Unauthenticated: the token is missing the read scope/)).toBeInTheDocument();
    expect(screen.getByText("The applications could not be read")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/import/proposal")).toHaveLength(0);
    expect(screen.queryByText(/tok-secret-123/)).not.toBeInTheDocument();
  });

  it("does not offer to go on with what has no application to create, and gives the command that does", { timeout: 20_000 }, async () => {
    const equivalent = "noust create shop.example.com --type container --image ghcr.io/acme/shop:1.4";
    const { harness } = remote({
      "POST /api/import/proposal": () => json(200, { ...PROPOSAL, deployable: false, proposal: { ...PROPOSAL.proposal, equivalent } }),
    });
    const user = await startFromPlatform(harness);
    await readServer(user);
    await user.click(await screen.findByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: /shop/ }));

    await user.click(screen.getByRole("button", { name: "Read the application" }));

    expect(await screen.findByText(equivalent)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findAllByText("Read an application first, or choose an export file.")).not.toHaveLength(0);
    expect(screen.getByRole("heading", { level: 2, name: "Source" })).toBeInTheDocument();
  });

  it("reads a Dokploy server the same way", { timeout: 20_000 }, async () => {
    const { backend, harness } = remote();
    const user = await startFromPlatform(harness, "Dokploy");

    await readServer(user, "https://dokploy.acme.dev", "dk-token");

    await waitFor(() => {
      expect(backend.callsTo("POST /api/import/applications")[0]?.body).toEqual({
        platform: "dokploy",
        url: "https://dokploy.acme.dev",
        token: "dk-token",
      });
    });
  });

  it("is still an export file when that is chosen", { timeout: 20_000 }, async () => {
    const { harness } = remote();
    const user = await startFromPlatform(harness, "An export file");

    expect(screen.getByLabelText("Export file")).toBeInTheDocument();
    expect(screen.queryByLabelText("API token")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Coolify" }));
    expect(screen.queryByLabelText("Export file")).not.toBeInTheDocument();
  });

  it("has no accessibility violations while it asks for a server and while it lists what it has", { timeout: 25_000 }, async () => {
    const { harness } = remote();
    const user = await startFromPlatform(harness);
    await expectNoAxeViolations(screen.getByRole("main"));

    await readServer(user);
    await screen.findByRole("combobox", { name: "Application" });

    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("speaks Spanish", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    const { harness } = remote();
    await screen.findByRole("heading", { level: 1, name: "Nueva aplicación" });
    await harness.user.click(await screen.findByRole("radio", { name: "Importar una aplicación" }));
    await harness.user.click(await screen.findByRole("radio", { name: "Coolify" }));

    expect(screen.getByLabelText("Dirección del servidor")).toBeInTheDocument();
    expect(screen.getByLabelText("Token de API")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Leer las aplicaciones" })).toBeDisabled();
    expect(within(screen.getByRole("main")).getByText(/solo para estas dos peticiones/)).toBeInTheDocument();
  });
});
