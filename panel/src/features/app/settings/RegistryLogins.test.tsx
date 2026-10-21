import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { usesRegistries } from "./RegistryLogins";
import { DOMAIN, ELEVATED, RELEASE_APP, part, renderSettings, saveBar } from "./testKit";

const COMPOSE_APP = { ...RELEASE_APP, app_type: "docker-compose", layout: "inplace", follow_tags: null };

const LOGIN = {
  kind: "dockerhub",
  server: "docker.io",
  created_at: "2026-10-01T09:00:00+00:00",
  created_by: "root",
  updated_at: "2026-10-01T09:00:00+00:00",
  last_tested_at: null,
  last_status: null,
  last_error: null,
  apps: [] as string[],
};

const ACCOUNTS = {
  logins: [
    { ...LOGIN, name: "hub", username: "acme" },
    { ...LOGIN, name: "ghcr-acme", kind: "ghcr", server: "ghcr.io", username: "acme-bot" },
  ],
  kinds: ["dockerhub", "ghcr", "custom"],
};

function compose(attached: string[] | (() => string[]), accounts: object = ACCOUNTS, routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/compose", "Compose", COMPOSE_APP, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    "GET /api/registries": () => json(200, accounts),
    [`GET /api/apps/${DOMAIN}/registry-logins`]: () => json(200, { domain: DOMAIN, logins: typeof attached === "function" ? attached() : attached }),
    ...routes,
  });
}

const card = () => part("Registry accounts");

describe("which applications have registry accounts", () => {
  it("are the ones that pull or build images", () => {
    expect(usesRegistries({ app_type: "docker-compose" })).toBe(true);
    expect(usesRegistries({ app_type: "container" })).toBe(true);
    expect(usesRegistries({ app_type: "nextjs" })).toBe(false);
    expect(usesRegistries({ app_type: null })).toBe(false);
  });
});

describe("the registry accounts of an application", () => {
  it("shows which are attached and has nothing to save yet", { timeout: 40_000 }, async () => {
    const { container } = await compose(["hub"]);
    await screen.findByRole("heading", { name: "Registry accounts" });
    const form = within(card());

    expect(await form.findByRole("checkbox", { name: /^hub/ })).toBeChecked();
    expect(form.getByRole("checkbox", { name: /^ghcr-acme/ })).not.toBeChecked();
    expect(within(saveBar()).getByRole("button", { name: "Save" })).toBeDisabled();
    await expectNoAxeViolations(container);
  });

  it("sends the chosen names and shows what the server stored", { timeout: 40_000 }, async () => {
    let stored: string[] = [];
    const { user, backend } = await compose(() => stored, ACCOUNTS, {
      [`PUT /api/apps/${DOMAIN}/registry-logins`]: (call) => {
        stored = (call.body as { logins: string[] }).logins;
        return json(200, { domain: DOMAIN, logins: stored });
      },
    });
    await screen.findByRole("heading", { name: "Registry accounts" });
    const form = within(card());
    await user.click(await form.findByRole("checkbox", { name: /^ghcr-acme/ }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/registry-logins`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/registry-logins`)[0]?.body).toEqual({ logins: ["ghcr-acme"] });
    await waitFor(() => expect(within(saveBar()).getByRole("button", { name: "Save" })).toBeDisabled());
    expect(form.getByRole("checkbox", { name: /^ghcr-acme/ })).toBeChecked();
  });

  it("points to where an account is added when the server has none", { timeout: 40_000 }, async () => {
    await compose([], { logins: [], kinds: ACCOUNTS.kinds });
    await screen.findByRole("heading", { name: "Registry accounts" });

    expect(await within(card()).findByRole("link", { name: "Manage the accounts" })).toHaveAttribute("href", "/settings/integrations");
  });

  it("is not shown for an application that neither pulls nor builds images", { timeout: 30_000 }, async () => {
    const { backend } = await renderSettings("/compose", "Compose", RELEASE_APP, {});

    expect(screen.queryByRole("heading", { name: "Registry accounts" })).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/registry-logins`)).toHaveLength(0);
  });
});
