import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { goToGitLab } from "./gitlab";

// The browser is not really sent anywhere: what it was asked to go to is asserted instead.
vi.mock("./gitlab", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./gitlab")>();
  return { ...actual, goToGitLab: vi.fn(() => true) };
});

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
beforeEach(() => {
  vi.mocked(goToGitLab).mockClear();
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

const WORK = {
  name: "work",
  host: "gitlab.com",
  kind: "token",
  username: "yago",
  created_at: "2026-10-01T09:00:00+00:00",
  created_by: "root",
  updated_at: "2026-10-01T09:00:00+00:00",
  last_checked_at: "2026-10-02T09:00:00+00:00",
  last_status: "ok",
  last_error: null,
};

const BROKEN = { ...WORK, name: "old", kind: "oauth", host: "git.example.com", last_status: "failed", last_error: "401 Unauthorized: the token expired" };

const STATUS = {
  connections: [WORK, BROKEN],
  oauth_apps: [{ host: "git.example.com", client_id: "abc123", created_at: "2026-10-01T09:00:00+00:00", created_by: "root" }],
  redirect_uri: "https://console.example.com/integrations/gitlab/callback",
};

async function integrations(routes: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(ELEVATED),
    "GET /api/integrations/github": () => json(200, { configured: false, installations: [], hooks_url: null, hooks_active: false, host: "github.com" }),
    "GET /api/config": () => json(200, { config: { web: { hooks_url: "" } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
    "GET /api/deploy-keys/hosts": () => json(200, { hosts: [], total: 0 }),
    "GET /api/registries": () => json(200, { logins: [], kinds: [] }),
    "GET /api/integrations/gitlab": () => json(200, STATUS),
    ...routes,
  });
  const harness = renderConsole("/settings/integrations");
  const heading = await screen.findByRole("heading", { level: 2, name: "GitLab" });
  const section = heading.closest("section");
  if (!section) throw new Error("no GitLab section");
  return { ...harness, backend, section };
}

describe("the GitLab connections of the server", () => {
  it("lists each with its host, how it signs in and what the last check said", { timeout: 40_000 }, async () => {
    const { section, container } = await integrations();
    const table = await within(section).findByRole("table", { name: "GitLab connections" });
    const work = within(table).getByText("work").closest("tr");
    if (!work) throw new Error("no row for work");
    expect(within(work).getByText("Access token")).toBeInTheDocument();
    expect(within(work).getByText("gitlab.com")).toBeInTheDocument();
    expect(within(work).getByText("yago")).toBeInTheDocument();
    expect(within(work).getByText("Works")).toBeInTheDocument();

    const old = within(table).getByText("old").closest("tr");
    if (!old) throw new Error("no row for old");
    expect(within(old).getByText("OAuth sign-in")).toBeInTheDocument();
    expect(within(old).getByText("Failed")).toBeInTheDocument();
    // What GitLab answered, as it said it.
    expect(within(old).getByText("401 Unauthorized: the token expired")).toBeInTheDocument();
    expect(within(section).getByRole("table", { name: "GitLab OAuth applications" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says GitLab is not connected when nothing is", async () => {
    const { section } = await integrations({ "GET /api/integrations/gitlab": () => json(200, { connections: [], oauth_apps: [], redirect_uri: null }) });
    expect(await within(section).findByText("GitLab is not connected")).toBeInTheDocument();
  });

  it("says when they cannot be read", async () => {
    const { section } = await integrations({ "GET /api/integrations/gitlab": () => problem(500, "internal_error", "The GitLab store is unreadable") });
    expect(await within(section).findByText("The GitLab store is unreadable")).toBeInTheDocument();
  });

  it("connects with a token, sent once, and lists the new connection", { timeout: 40_000 }, async () => {
    let connections: object[] = [WORK];
    const { user, backend, section } = await integrations({
      "GET /api/integrations/gitlab": () => json(200, { ...STATUS, connections }),
      "POST /api/integrations/gitlab/connections": (call) => {
        const body = call.body as { name: string; host: string };
        const made = { ...WORK, name: body.name, host: body.host, username: "ci", last_checked_at: null, last_status: null };
        connections = [...connections, made];
        return json(201, made);
      },
    });
    await user.click(await within(section).findByRole("button", { name: "Connect GitLab" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect GitLab" });
    expect(within(dialog).getByRole("button", { name: "Connect" })).toBeDisabled();

    await user.type(within(dialog).getByLabelText(/^Name/), "ci");
    await user.clear(within(dialog).getByLabelText(/^GitLab address/));
    await user.type(within(dialog).getByLabelText(/^GitLab address/), "https://GitLab.Example.com/");
    await user.type(within(dialog).getByLabelText(/^Access token/), "glpat-secret");
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(backend.callsTo("POST /api/integrations/gitlab/connections")).toHaveLength(1));
    // The host is sent the way it is compared: no scheme, no path, lower case.
    expect(backend.callsTo("POST /api/integrations/gitlab/connections")[0]?.body).toEqual({ name: "ci", host: "gitlab.example.com", token: "glpat-secret" });
    expect((await within(section).findAllByText("ci")).length).toBeGreaterThan(0);
  });

  it("puts GitLab's refusal of the token in the form and keeps what was typed", { timeout: 40_000 }, async () => {
    const { user, section } = await integrations({
      "POST /api/integrations/gitlab/connections": () => problem(400, "integrationerror", "GitLab refused the token: 401 Unauthorized"),
    });
    await user.click(await within(section).findByRole("button", { name: "Connect GitLab" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect GitLab" });
    await user.type(within(dialog).getByLabelText(/^Name/), "ci");
    await user.type(within(dialog).getByLabelText(/^Access token/), "bad");
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));

    expect(await within(dialog).findByText(/GitLab refused the token: 401 Unauthorized/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/^Name/)).toHaveValue("ci");
  });

  it("signs in with GitLab through the application already registered for the host", { timeout: 40_000 }, async () => {
    const { user, backend, section } = await integrations({
      "POST /api/integrations/gitlab/oauth/authorize": () => json(200, { authorize_url: "https://git.example.com/oauth/authorize?client_id=abc123&state=s1", state: "s1" }),
    });
    await user.click(await within(section).findByRole("button", { name: "Connect GitLab" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect GitLab" });
    await user.click(within(dialog).getByRole("radio", { name: "Sign in with GitLab" }));
    await user.type(within(dialog).getByLabelText(/^Name/), "team");
    await user.clear(within(dialog).getByLabelText(/^GitLab address/));
    await user.type(within(dialog).getByLabelText(/^GitLab address/), "git.example.com");

    expect(await within(dialog).findByText(/An OAuth application is registered for git\.example\.com/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Sign in with GitLab" }));

    await waitFor(() => expect(goToGitLab).toHaveBeenCalledWith("https://git.example.com/oauth/authorize?client_id=abc123&state=s1"));
    expect(backend.callsTo("POST /api/integrations/gitlab/oauth/authorize")[0]?.body).toEqual({ name: "team", host: "git.example.com" });
    // The application already exists: nothing is registered again.
    expect(backend.callsTo("PUT /api/integrations/gitlab/oauth-apps")).toHaveLength(0);
  });

  it("registers the OAuth application first when the host has none, and says where GitLab returns", { timeout: 40_000 }, async () => {
    const { user, backend, section } = await integrations({
      "PUT /api/integrations/gitlab/oauth-apps": () => json(200, { host: "gitlab.com", client_id: "id1", created_at: "2026-10-03T09:00:00+00:00", created_by: "root" }),
      "POST /api/integrations/gitlab/oauth/authorize": () => json(200, { authorize_url: "https://gitlab.com/oauth/authorize?state=s2", state: "s2" }),
    });
    await user.click(await within(section).findByRole("button", { name: "Connect GitLab" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect GitLab" });
    await user.click(within(dialog).getByRole("radio", { name: "Sign in with GitLab" }));
    await user.type(within(dialog).getByLabelText(/^Name/), "team");

    expect(within(dialog).getByText("https://console.example.com/integrations/gitlab/callback")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/^Application ID/), "id1");
    await user.type(within(dialog).getByLabelText(/^Secret/), "s3cret");
    await user.click(within(dialog).getByRole("button", { name: "Register it and sign in" }));

    await waitFor(() => expect(goToGitLab).toHaveBeenCalledWith("https://gitlab.com/oauth/authorize?state=s2"));
    expect(backend.callsTo("PUT /api/integrations/gitlab/oauth-apps")[0]?.body).toEqual({ host: "gitlab.com", client_id: "id1", client_secret: "s3cret" });
  });

  it("explains that a sign-in needs a public address when the console has none", { timeout: 40_000 }, async () => {
    const { user, section } = await integrations({
      "GET /api/integrations/gitlab": () => json(200, { ...STATUS, redirect_uri: null }),
    });
    await user.click(await within(section).findByRole("button", { name: "Connect GitLab" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect GitLab" });
    await user.click(within(dialog).getByRole("radio", { name: "Sign in with GitLab" }));

    expect(within(dialog).getByText(/no public address for GitLab to send the browser back to/)).toBeInTheDocument();
    expect(within(dialog).getByText(/noust config set web\.public_url/)).toBeInTheDocument();
  });

  it("checks a connection and shows GitLab's answer", { timeout: 40_000 }, async () => {
    let checked = false;
    const { user, backend, section } = await integrations({
      "GET /api/integrations/gitlab": () => json(200, { ...STATUS, connections: [checked ? { ...BROKEN, last_status: "ok", last_error: null } : BROKEN] }),
      "POST /api/integrations/gitlab/connections/old/test": () => {
        checked = true;
        return json(200, { ...BROKEN, last_status: "ok", last_error: null });
      },
    });
    const row = (await within(section).findByText("old")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for old" }));
    await user.click(await screen.findByRole("menuitem", { name: "Check the connection" }));

    await waitFor(() => expect(backend.callsTo("POST /api/integrations/gitlab/connections/old/test")).toHaveLength(1));
    expect(await within(section).findByText("Works")).toBeInTheDocument();
    expect(within(section).queryByText(/token expired/)).not.toBeInTheDocument();
  });

  it("removes a connection after asking", { timeout: 40_000 }, async () => {
    let connections: object[] = [WORK];
    const { user, backend, section } = await integrations({
      "GET /api/integrations/gitlab": () => json(200, { ...STATUS, connections, oauth_apps: [] }),
      "DELETE /api/integrations/gitlab/connections/work": () => {
        connections = [];
        return json(200, { removed: true });
      },
    });
    const row = (await within(section).findByText("work")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for work" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const confirm = await screen.findByRole("alertdialog");
    expect(within(confirm).getByText(/Noust forgets its token/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Remove the connection" }));

    await waitFor(() => expect(backend.callsTo("DELETE /api/integrations/gitlab/connections/work")).toHaveLength(1));
    expect(await within(section).findByText("GitLab is not connected")).toBeInTheDocument();
  });

  it("removes an OAuth application after asking", { timeout: 40_000 }, async () => {
    const { user, backend, section } = await integrations({
      "DELETE /api/integrations/gitlab/oauth-apps/git.example.com": () => json(200, { removed: true }),
    });
    await user.click(await within(section).findByRole("button", { name: "Remove the application of git.example.com" }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Remove the application" }));

    await waitFor(() => expect(backend.callsTo("DELETE /api/integrations/gitlab/oauth-apps/git.example.com")).toHaveLength(1));
  });
});
