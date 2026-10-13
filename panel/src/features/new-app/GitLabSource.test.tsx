import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import type { Inspection } from "./wizard";

const CONNECTION = {
  name: "work",
  host: "gitlab.com",
  kind: "token",
  username: "yago",
  created_at: "2026-10-01T09:00:00+00:00",
  created_by: "root",
  updated_at: "2026-10-01T09:00:00+00:00",
  last_checked_at: null as string | null,
  last_status: null as string | null,
  last_error: null as string | null,
};

const STATUS = { connections: [CONNECTION], oauth_apps: [], redirect_uri: null };

const PROJECTS = {
  items: [
    { id: 11, path: "acme/api", default_branch: "main", clone_url: "https://gitlab.com/acme/api.git", web_url: "https://gitlab.com/acme/api" },
    { id: 12, path: "acme/shop/storefront", default_branch: "production", clone_url: "https://gitlab.com/acme/shop/storefront.git", web_url: "https://gitlab.com/acme/shop/storefront" },
  ],
  total: 2,
};

const GROUPS = { items: [{ id: 1, full_path: "acme", name: "Acme" }, { id: 2, full_path: "acme/shop", name: "Shop" }], total: 2 };

const BRANCHES = {
  items: [
    { name: "production", default: true, commit: "4f2a9c1" },
    { name: "staging", default: false, commit: "9b1e22d" },
  ],
  total: 2,
};

const INSPECTION: Inspection = {
  app_type: "nextjs",
  detected_types: ["nextjs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  default_port: 3000,
  env_keys: [],
  branch: "staging",
  commit: "9b1e22d",
};

function wizard(status: object | null, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
    "GET /api/integrations/github": () => json(200, { configured: false, installations: [], hooks_url: null, hooks_active: false, host: "github.com" }),
    ...(status !== null ? { "GET /api/integrations/gitlab": () => json(200, status) } : {}),
    "GET /api/integrations/gitlab/connections/work/groups": () => json(200, GROUPS),
    "GET /api/integrations/gitlab/connections/work/projects": () => json(200, PROJECTS),
    "GET /api/integrations/gitlab/connections/work/projects/12/branches": () => json(200, BRANCHES),
    "POST /api/apps/inspect": () => json(200, INSPECTION),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

describe("the new-app wizard, from GitLab", () => {
  it("is offered only where a connection exists", { timeout: 30_000 }, async () => {
    wizard({ connections: [], oauth_apps: [], redirect_uri: null });
    expect(await screen.findByLabelText("Repository or directory")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "From GitLab" })).not.toBeInTheDocument();
  });

  it("stays on the typed source when the integration cannot be read", { timeout: 30_000 }, async () => {
    const { backend } = wizard(null);
    expect(await screen.findByLabelText("Repository or directory")).toBeInTheDocument();
    await waitFor(() => expect(backend.callsTo("GET /api/integrations/gitlab")).toHaveLength(1));
    expect(screen.queryByRole("radio", { name: "From GitLab" })).not.toBeInTheDocument();
  });

  it("lists the projects, narrows them by group and by name, and passes axe", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard(STATUS);
    await harness.user.click(await screen.findByRole("radio", { name: "From GitLab" }));
    const list = await screen.findByRole("list", { name: "Projects" });
    expect(within(list).getByText("acme/api")).toBeInTheDocument();
    expect(within(list).getByText("acme/shop/storefront")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);

    await harness.user.type(screen.getByLabelText(/^Search projects/), "store");
    await waitFor(() => expect(backend.callsTo("GET /api/integrations/gitlab/connections/work/projects").some((call) => call.search.get("search") === "store")).toBe(true));
  });

  it("chooses a project, preselects its default branch, and inspects its clone address", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard(STATUS);
    const { user } = harness;
    await user.click(await screen.findByRole("radio", { name: "From GitLab" }));
    await user.click(await screen.findByRole("button", { name: /acme\/shop\/storefront/ }));

    // Chosen: the project stands in place of the list, focus on its control.
    expect(await screen.findByRole("button", { name: "Change project" })).toHaveFocus();
    const branch = screen.getByRole("combobox", { name: "Branch" });
    await waitFor(() => expect(branch).toHaveTextContent("production"));
    await user.click(branch);
    await user.click(await screen.findByRole("option", { name: /staging/ }));
    expect(branch).toHaveTextContent("staging");

    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    expect(backend.callsTo("POST /api/apps/inspect").at(-1)?.body).toEqual({ source: "https://gitlab.com/acme/shop/storefront.git", branch: "staging" });
  });

  it("types the branch when GitLab cannot list them", { timeout: 40_000 }, async () => {
    const { harness } = wizard(STATUS, {
      "GET /api/integrations/gitlab/connections/work/projects/12/branches": () => problem(502, "integrationerror", "GitLab answered 502 Bad Gateway"),
    });
    await harness.user.click(await screen.findByRole("radio", { name: "From GitLab" }));
    await harness.user.click(await screen.findByRole("button", { name: /acme\/shop\/storefront/ }));

    expect(await screen.findByText("GitLab answered 502 Bad Gateway")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Branch" })).toHaveValue("production");
  });

  it("says why the projects cannot be listed, and offers to try again", { timeout: 40_000 }, async () => {
    const { harness } = wizard(STATUS, {
      "GET /api/integrations/gitlab/connections/work/projects": () => problem(502, "integrationerror", "GitLab refused the token: 401 Unauthorized"),
    });
    await harness.user.click(await screen.findByRole("radio", { name: "From GitLab" }));

    expect(await screen.findByText("GitLab refused the token: 401 Unauthorized")).toBeInTheDocument();
    expect(screen.getByText("Could not list the GitLab projects")).toBeInTheDocument();
  });

  it("asks for a project before inspecting", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard(STATUS);
    await harness.user.click(await screen.findByRole("radio", { name: "From GitLab" }));
    await screen.findByRole("list", { name: "Projects" });
    await harness.user.click(screen.getByRole("button", { name: "Inspect source" }));

    expect((await screen.findAllByText("Choose a project.")).length).toBeGreaterThan(0);
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(0);
  });
});
