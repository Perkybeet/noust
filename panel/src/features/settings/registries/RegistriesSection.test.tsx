import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { kindWords, needsServer, passwordLabelKey, usernameLabelKey } from "./registries";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
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

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const KINDS = ["dockerhub", "ghcr", "gitlab", "ecr", "acr", "gar", "custom"];

const HUB = {
  name: "hub",
  kind: "dockerhub",
  server: "docker.io",
  username: "acme",
  created_at: "2026-10-01T09:00:00+00:00",
  created_by: "root",
  updated_at: "2026-10-01T09:00:00+00:00",
  last_tested_at: "2026-10-02T09:00:00+00:00",
  last_status: "ok",
  last_error: null,
  apps: ["shop.example.com", "api.example.com", "docs.example.com"],
};

const BROKEN = {
  ...HUB,
  name: "ghcr-acme",
  kind: "ghcr",
  server: "ghcr.io",
  last_status: "failed",
  last_error: "Error response from daemon: Get https://ghcr.io/v2/: denied",
  apps: [] as string[],
};

async function integrations(routes: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(ELEVATED),
    "GET /api/integrations/github": () => json(200, { configured: false, installations: [], hooks_url: null, hooks_active: false }),
    "GET /api/config": () => json(200, { config: { web: { hooks_url: "" } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
    "GET /api/deploy-keys/hosts": () => json(200, { hosts: [], total: 0 }),
    "GET /api/registries": () => json(200, { logins: [HUB, BROKEN], kinds: KINDS }),
    ...routes,
  });
  const harness = renderConsole("/settings/integrations");
  const heading = await screen.findByRole("heading", { level: 2, name: "Container registries" });
  const section = heading.closest("section");
  if (!section) throw new Error("no registries section");
  return { ...harness, backend, section };
}

describe("the words of a registry account", () => {
  it("names the kinds, and leaves one it does not know as the server wrote it", () => {
    const t = ((key: string) => key) as unknown as Parameters<typeof kindWords>[0];
    expect(kindWords(t, "ghcr")).toBe("registries.kind.ghcr");
    expect(kindWords(t, "quay")).toBe("quay");
  });

  it("asks for the host only of the kinds that have none of their own", () => {
    expect(["dockerhub", "ghcr", "gitlab"].some(needsServer)).toBe(false);
    expect(["ecr", "acr", "gar", "custom"].every(needsServer)).toBe(true);
  });

  it("calls ECR's account and password what AWS calls them", () => {
    expect(usernameLabelKey("ecr")).toBe("registries.dialog.usernameEcr");
    expect(passwordLabelKey("ecr")).toBe("registries.dialog.passwordEcr");
    expect(usernameLabelKey("ghcr")).toBe("registries.dialog.username");
    expect(passwordLabelKey("ghcr")).toBe("registries.dialog.password");
  });
});

describe("the registry accounts of the server, in Settings", () => {
  it("lists each account with its registry, its last test and the applications that use it", { timeout: 40_000 }, async () => {
    const { section, container } = await integrations();
    const table = await within(section).findByRole("table", { name: "Registry accounts" });
    const hub = within(table).getByText("hub").closest("tr");
    if (!hub) throw new Error("no row for hub");
    expect(within(hub).getByText("Docker Hub")).toBeInTheDocument();
    expect(within(hub).getByText("docker.io")).toBeInTheDocument();
    expect(within(hub).getByText("Logged in")).toBeInTheDocument();
    expect(within(hub).getByRole("link", { name: "shop.example.com" })).toHaveAttribute("href", "/apps/shop.example.com/settings/deploys");
    expect(within(hub).queryByRole("link", { name: "docs.example.com" })).not.toBeInTheDocument();
    expect(within(hub).getByText("and 1 more")).toBeInTheDocument();

    const broken = within(table).getByText("ghcr-acme").closest("tr");
    if (!broken) throw new Error("no row for ghcr-acme");
    expect(within(broken).getByText("Failed")).toBeInTheDocument();
    // What the registry said, as it said it.
    expect(within(broken).getByText("Error response from daemon: Get https://ghcr.io/v2/: denied")).toBeInTheDocument();
    expect(within(broken).getByText("None yet")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says there are none yet", async () => {
    const { section } = await integrations({ "GET /api/registries": () => json(200, { logins: [], kinds: KINDS }) });
    expect(await within(section).findByText("No registry accounts")).toBeInTheDocument();
  });

  it("says when they cannot be read", async () => {
    const { section } = await integrations({ "GET /api/registries": () => problem(500, "internal_error", "The registry store is unreadable") });
    expect(await within(section).findByText("The registry store is unreadable")).toBeInTheDocument();
  });

  it("adds an account and sends the password once, to the server only", { timeout: 40_000 }, async () => {
    let logins: object[] = [HUB];
    const { user, backend, section } = await integrations({
      "GET /api/registries": () => json(200, { logins, kinds: KINDS }),
      "POST /api/registries": (call) => {
        const body = call.body as { name: string; kind: string; username: string };
        const made = { ...HUB, name: body.name, kind: body.kind, username: body.username, last_status: null, last_tested_at: null, apps: [] as string[] };
        logins = [...logins, made];
        return json(201, made);
      },
    });
    await user.click(await within(section).findByRole("button", { name: "Add an account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a registry account" });
    expect(within(dialog).getByRole("button", { name: "Add the account" })).toBeDisabled();

    await user.type(within(dialog).getByLabelText(/^Name/), "hub-ci");
    await user.type(within(dialog).getByLabelText(/^Username/), "ci-bot");
    await user.type(within(dialog).getByLabelText(/^Password or token/), "s3cret-token");
    await user.click(within(dialog).getByRole("button", { name: "Add the account" }));

    await waitFor(() => expect(backend.callsTo("POST /api/registries")).toHaveLength(1));
    expect(backend.callsTo("POST /api/registries")[0]?.body).toEqual({ name: "hub-ci", kind: "dockerhub", username: "ci-bot", password: "s3cret-token" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add a registry account" })).not.toBeInTheDocument());
    expect(await within(section).findByText("hub-ci")).toBeInTheDocument();
  });

  it("puts the server's refusal beside its field and keeps what was typed", { timeout: 40_000 }, async () => {
    const { user, section } = await integrations({
      "POST /api/registries": () =>
        json(400, { error: "validation_error", detail: "name: 'Hub' is not a valid name", hint: "Use lowercase letters.", fields: { name: "'Hub' is not a valid name" } }),
    });
    await user.click(await within(section).findByRole("button", { name: "Add an account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a registry account" });
    await user.type(within(dialog).getByLabelText(/^Name/), "Hub");
    await user.type(within(dialog).getByLabelText(/^Username/), "ci-bot");
    await user.type(within(dialog).getByLabelText(/^Password or token/), "pw");
    await user.click(within(dialog).getByRole("button", { name: "Add the account" }));

    expect(await within(dialog).findByText(/'Hub' is not a valid name/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/^Name/)).toHaveValue("Hub");
  });

  it("sends only what an edit changed, and keeps the password when it is left empty", { timeout: 40_000 }, async () => {
    const { user, backend, section } = await integrations({
      "PATCH /api/registries/hub": () => json(200, { ...HUB, username: "acme-ci" }),
    });
    const row = (await within(section).findByText("hub")).closest("tr");
    if (!row) throw new Error("no row for hub");
    await user.click(within(row).getByRole("button", { name: "Actions for hub" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit hub" });
    const username = within(dialog).getByLabelText(/^Username/);
    await user.clear(username);
    await user.type(username, "acme-ci");
    await user.click(within(dialog).getByRole("button", { name: "Save the changes" }));

    await waitFor(() => expect(backend.callsTo("PATCH /api/registries/hub")).toHaveLength(1));
    expect(backend.callsTo("PATCH /api/registries/hub")[0]?.body).toEqual({ username: "acme-ci" });
  });

  it("tests the login and shows the registry's answer", { timeout: 40_000 }, async () => {
    let tested = false;
    const { user, backend, section } = await integrations({
      "GET /api/registries": () => json(200, { logins: [tested ? { ...BROKEN, last_status: "ok", last_error: null } : BROKEN], kinds: KINDS }),
      "POST /api/registries/ghcr-acme/test": () => {
        tested = true;
        return json(200, { ...BROKEN, last_status: "ok", last_error: null });
      },
    });
    const row = (await within(section).findByText("ghcr-acme")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for ghcr-acme" }));
    await user.click(await screen.findByRole("menuitem", { name: "Test the login" }));

    await waitFor(() => expect(backend.callsTo("POST /api/registries/ghcr-acme/test")).toHaveLength(1));
    expect(await within(section).findByText("Logged in")).toBeInTheDocument();
    expect(within(section).queryByText(/denied/)).not.toBeInTheDocument();
  });

  it("removes an account after asking, and says how many applications lose it", { timeout: 40_000 }, async () => {
    let logins: object[] = [HUB];
    const { user, backend, section } = await integrations({
      "GET /api/registries": () => json(200, { logins, kinds: KINDS }),
      "DELETE /api/registries/hub": () => {
        logins = [];
        return json(200, { name: "hub", apps: ["shop.example.com", "api.example.com", "docs.example.com"] });
      },
    });
    const row = (await within(section).findByText("hub")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for hub" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const confirm = await screen.findByRole("alertdialog");
    expect(within(confirm).getByText(/pull and build without an account/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Remove the account" }));

    await waitFor(() => expect(backend.callsTo("DELETE /api/registries/hub")).toHaveLength(1));
    expect(await within(section).findByText("No registry accounts")).toBeInTheDocument();
  });
});
