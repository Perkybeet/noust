import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GitHubStatus } from "../../../api/queries/github";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { hostField, statusHost } from "./GitHubHost";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const NOT_CONFIGURED: GitHubStatus = { configured: false, installations: [], hooks_url: null, hooks_active: false, host: "github.com" };

const CONFIGURED: GitHubStatus = {
  configured: true,
  app_id: 7,
  slug: "noust-ghe",
  name: "Noust GHE",
  owner: "acme",
  html_url: "https://ghe.example.com/apps/noust-ghe",
  settings_url: "https://ghe.example.com/settings/apps/noust-ghe",
  install_url: "https://ghe.example.com/apps/noust-ghe/installations/new",
  installations: [],
  hooks_url: null,
  hooks_active: false,
  host: "ghe.example.com",
};

function integrations(status: () => GitHubStatus, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(ELEVATED),
    "GET /api/integrations/github": () => json(200, status()),
    "GET /api/config": () => json(200, { config: { web: { hooks_url: "" } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
    "GET /api/deploy-keys/hosts": () => json(200, { hosts: [], total: 0 }),
    "GET /api/registries": () => json(200, { logins: [], kinds: [] }),
    ...extra,
  });
  const harness = renderConsole("/settings/integrations");
  return { ...harness, backend };
}

describe("the GitHub address field", () => {
  it("is empty for github.com, which is what leaving it empty means", () => {
    expect(hostField("github.com")).toBe("");
    expect(hostField("ghe.example.com")).toBe("ghe.example.com");
  });

  it("takes a status from a server older than the field to be github.com", () => {
    expect(statusHost({ host: "ghe.example.com" })).toBe("ghe.example.com");
    expect(statusHost({} as { host: string })).toBe("github.com");
  });
});

describe("which GitHub the integration talks to", () => {
  it("is asked before the App is created, empty for github.com", { timeout: 40_000 }, async () => {
    const { container } = integrations(() => NOT_CONFIGURED);
    const field = await screen.findByLabelText(/^GitHub address/);

    expect(field).toHaveValue("");
    expect(screen.getByRole("button", { name: "Use this GitHub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create GitHub App" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("sends an Enterprise host and shows the one now in force", { timeout: 40_000 }, async () => {
    let current: GitHubStatus = NOT_CONFIGURED;
    const { user, backend } = integrations(() => current, {
      "PUT /api/integrations/github/host": (call) => {
        const body = call.body as { host: string | null };
        current = { ...NOT_CONFIGURED, host: body.host ?? "github.com" };
        return json(200, current);
      },
    });
    await user.type(await screen.findByLabelText(/^GitHub address/), "ghe.example.com");
    await user.click(screen.getByRole("button", { name: "Use this GitHub" }));

    await waitFor(() => expect(backend.callsTo("PUT /api/integrations/github/host")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/integrations/github/host")[0]?.body).toEqual({ host: "ghe.example.com", force: false });
    await waitFor(() => expect(screen.getByRole("button", { name: "Use this GitHub" })).toBeDisabled());
    expect(screen.getByLabelText(/^GitHub address/)).toHaveValue("ghe.example.com");
  });

  it("sends null to go back to github.com", { timeout: 40_000 }, async () => {
    const { user, backend } = integrations(() => ({ ...NOT_CONFIGURED, host: "ghe.example.com" }), {
      "PUT /api/integrations/github/host": () => json(200, NOT_CONFIGURED),
    });
    await user.clear(await screen.findByLabelText(/^GitHub address/));
    await user.click(screen.getByRole("button", { name: "Use this GitHub" }));

    await waitFor(() => expect(backend.callsTo("PUT /api/integrations/github/host")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/integrations/github/host")[0]?.body).toEqual({ host: null, force: false });
  });

  it("shows the server's refusal beside the field and keeps what was typed", { timeout: 40_000 }, async () => {
    const { user } = integrations(() => NOT_CONFIGURED, {
      "PUT /api/integrations/github/host": () =>
        json(400, { error: "configerror", detail: "10.0.0.5 is a private address", hint: "Allow it first: noust config set notifications.allow_private_hosts true" }),
    });
    await user.type(await screen.findByLabelText(/^GitHub address/), "10.0.0.5");
    await user.click(screen.getByRole("button", { name: "Use this GitHub" }));

    expect(await screen.findByText(/10\.0\.0\.5 is a private address/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^GitHub address/)).toHaveValue("10.0.0.5");
  });

  it("is a fact of the App once it exists, not a field", { timeout: 40_000 }, async () => {
    integrations(() => CONFIGURED);
    const facts = await screen.findByText("The host the App lives on. It cannot change while the App exists.");

    expect(facts).toBeInTheDocument();
    expect(screen.queryByLabelText(/^GitHub address/)).not.toBeInTheDocument();
    expect(within(facts.closest("div") ?? document.body).getAllByText("ghe.example.com").length).toBeGreaterThan(0);
  });
});
