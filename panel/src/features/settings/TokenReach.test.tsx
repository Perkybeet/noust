import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { organisation } from "../teams/testFixtures";
import { NO_REACH, reachBody, reachReady } from "./tokenReach";

const NOW = Date.now() / 1000;

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

describe("the reach of a token", () => {
  it("names nothing for everything its owner can reach, and what it needs for each narrower choice", () => {
    expect(reachBody(NO_REACH)).toEqual({});
    expect(reachBody({ kind: "team", team: "acme", project: "", environment: "" })).toEqual({ team: "acme" });
    expect(reachBody({ kind: "project", team: "acme", project: "web", environment: "" })).toEqual({ team: "acme", project: "web" });
    expect(reachBody({ kind: "environment", team: "acme", project: "web", environment: "staging" })).toEqual({ team: "acme", project: "web", environment: "staging" });
    expect(reachReady({ kind: "project", team: "acme", project: "", environment: "" })).toBe(false);
    expect(reachReady(NO_REACH)).toBe(true);
  });
});

describe("Settings > API tokens bound to a project", () => {
  it("binds a new token to an environment and lists where each token reaches", { timeout: 30_000 }, async () => {
    const tokens: Record<string, unknown>[] = [
      { id: 1, name: "grafana", scope: "read", created_at: NOW - 86_400, expires_at: null, last_used_at: null, revoked_at: null, bound: null },
      { id: 2, name: "acme-ci", scope: "deploy", created_at: NOW - 3_600, expires_at: null, last_used_at: null, revoked_at: null, bound: "acme/web" },
    ];
    const backend = fakeBackend({
      ...signedInRoutes(),
      ...organisation(),
      "GET /api/auth/tokens": () => json(200, { tokens }),
      "GET /api/auth/roles": () => json(200, { roles: { viewer: ["self", "apps.read"], admin: ["self", "apps.read", "apps.deploy"] }, descriptions: {} }),
      "POST /api/auth/tokens": (call: RecordedCall) => {
        const body = call.body as Record<string, unknown>;
        return json(201, { id: 3, name: body["name"], scope: body["scope"], created_at: NOW, expires_at: null, bound: "acme/web/staging", token: "noust_tok_s3cr3t" });
      },
    });
    const { user } = renderConsole("/settings/tokens");
    await screen.findByText("acme-ci");
    const table = screen.getByRole("region", { name: "API tokens" });
    expect(within(table).getByText("acme/web")).toBeInTheDocument();
    expect(within(table).getByText("Everything its owner reaches")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create token" }));
    const dialog = await screen.findByRole("dialog", { name: "Create an API token" });
    await user.type(within(dialog).getByLabelText(/^Name/), "staging-ci");
    await user.click(await within(dialog).findByRole("radio", { name: /One environment/ }));
    await user.click(within(dialog).getByRole("combobox", { name: "Team" }));
    await user.click(await screen.findByRole("option", { name: "Acme Corp" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Project" }));
    await user.click(await screen.findByRole("option", { name: "Web" }));
    // Until it names an environment, the token cannot be made: pressing the button says what is missing.
    await user.click(within(dialog).getByRole("button", { name: "Create token" }));
    expect(await within(dialog).findByText("Choose the team, project or environment the token reaches.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Environment" }));
    await user.click(await screen.findByRole("option", { name: "Staging" }));
    expect(within(dialog).getByText(/sees that and nothing else of the server/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create token" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/auth/tokens")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/auth/tokens")[0]?.body).toMatchObject({ name: "staging-ci", team: "acme", project: "web", environment: "staging" });
  });

  it("offers no reach on a server with no teams to bind to", { timeout: 30_000 }, async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/auth/tokens": () => json(200, { tokens: [] }),
      "GET /api/auth/roles": () => json(200, { roles: { viewer: ["self", "apps.read"] }, descriptions: {} }),
    });
    const { user } = renderConsole("/settings/tokens");
    await user.click(await screen.findByRole("button", { name: "Create token" }));
    const dialog = await screen.findByRole("dialog", { name: "Create an API token" });
    expect(within(dialog).queryByText("What it can reach")).not.toBeInTheDocument();
  });
});
