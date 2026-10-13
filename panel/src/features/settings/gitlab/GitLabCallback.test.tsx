import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { normalizeHost, oauthAppFor, parseGitLabCallback } from "./gitlab";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const CONNECTION = {
  name: "team",
  host: "gitlab.com",
  kind: "oauth",
  username: "yago",
  created_at: "2026-10-01T09:00:00+00:00",
  created_by: "root",
  updated_at: "2026-10-01T09:00:00+00:00",
  last_checked_at: null,
  last_status: null,
  last_error: null,
};

function backendFor(extra: Record<string, RouteHandler> = {}) {
  return fakeBackend({
    ...signedInRoutes(ELEVATED),
    "GET /api/integrations/gitlab": () => json(200, { connections: [CONNECTION], oauth_apps: [], redirect_uri: null }),
    "GET /api/integrations/github": () => json(200, { configured: false, installations: [], hooks_url: null, hooks_active: false, host: "github.com" }),
    "GET /api/config": () => json(200, { config: { web: { hooks_url: "" } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
    "GET /api/deploy-keys/hosts": () => json(200, { hosts: [], total: 0 }),
    "GET /api/registries": () => json(200, { logins: [], kinds: [] }),
    ...extra,
  });
}

describe("what GitLab's redirect carries", () => {
  it("keeps the code and state exactly as sent", () => {
    expect(parseGitLabCallback("?code=abc&state=1e5")).toEqual({ kind: "finish", code: "abc", state: "1e5" });
    expect(parseGitLabCallback("code=00123&state=s")).toEqual({ kind: "finish", code: "00123", state: "s" });
  });

  it("tells an address with nothing in it from a callback", () => {
    expect(parseGitLabCallback("")).toEqual({ kind: "invalid" });
    expect(parseGitLabCallback("?code=abc")).toEqual({ kind: "invalid" });
    expect(parseGitLabCallback("?error=access_denied&state=s")).toEqual({ kind: "invalid" });
  });
});

describe("hosts", () => {
  it("are compared without a scheme, a path or case", () => {
    expect(normalizeHost(" https://GitLab.Example.com/group/ ")).toBe("gitlab.example.com");
    expect(normalizeHost("gitlab.com.")).toBe("gitlab.com");
  });

  it("find the OAuth application registered for them", () => {
    const status = { oauth_apps: [{ host: "gitlab.com", client_id: "a", created_at: "2026-10-01T09:00:00+00:00", created_by: null }] };
    expect(oauthAppFor(status, "GitLab.com")?.client_id).toBe("a");
    expect(oauthAppFor(status, "git.example.com")).toBeUndefined();
    expect(oauthAppFor(status, "")).toBeUndefined();
  });
});

describe("the GitLab callback", () => {
  it("finishes the sign-in with GitLab's code, once, and returns to Integrations", { timeout: 30_000 }, async () => {
    const backend = backendFor({ "POST /api/integrations/gitlab/oauth/callback": () => json(201, CONNECTION) });
    // A state that reads as a number must reach the server exactly as GitLab sent it.
    const { location } = renderConsole("/integrations/gitlab/callback?code=a1b2c3&state=1e5");

    await waitFor(() => expect(location().pathname).toBe("/settings/integrations"));
    expect(backend.callsTo("POST /api/integrations/gitlab/oauth/callback").map((call) => call.body)).toEqual([{ code: "a1b2c3", state: "1e5" }]);
  });

  it("shows what the server said when the sign-in cannot be finished, and sends nothing twice", { timeout: 30_000 }, async () => {
    const backend = backendFor({
      "POST /api/integrations/gitlab/oauth/callback": () => problem(400, "validationerror", "This GitLab callback does not belong to a sign-in started here"),
    });
    renderConsole("/integrations/gitlab/callback?code=a1b2c3&state=old");

    expect(await screen.findByText(/does not belong to a sign-in started here/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "GitLab" })).toBeInTheDocument();
    expect(screen.getByText("GitLab could not be connected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Integrations" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/integrations/gitlab/oauth/callback")).toHaveLength(1);
  });

  it("has nothing to finish when it is opened by hand", { timeout: 30_000 }, async () => {
    const backend = backendFor();
    renderConsole("/integrations/gitlab/callback");

    expect(await screen.findByText("Nothing to finish here")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/integrations/gitlab/oauth/callback")).toHaveLength(0);
  });
});
