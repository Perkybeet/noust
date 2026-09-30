import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GitHubRepository, GitHubStatus } from "../../api/queries/github";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { filterRepositories } from "./GitHubSource";
import type { Inspection } from "./wizard";

const STATUS: GitHubStatus = {
  configured: true,
  app_id: 424242,
  slug: "noust-web-01",
  name: "Noust web-01",
  owner: "acme",
  html_url: "https://github.com/apps/noust-web-01",
  settings_url: "https://github.com/organizations/acme/settings/apps/noust-web-01",
  install_url: "https://github.com/apps/noust-web-01/installations/new",
  installations: [
    { installation_id: 7001, account: "acme", account_type: "Organization", repository_selection: "selected", settings_url: null },
    { installation_id: 7002, account: "yago", account_type: "User", repository_selection: "all", settings_url: null },
  ],
  hooks_url: "https://hooks.example.com/hooks/github",
  hooks_active: true,
};

const REPOSITORIES: GitHubRepository[] = [
  { full_name: "acme/api", private: true, default_branch: "main", clone_url: "https://github.com/acme/api.git", source: "github:acme/api", installation_id: 7001 },
  { full_name: "acme/storefront", private: true, default_branch: "production", clone_url: "https://github.com/acme/storefront.git", source: "github:acme/storefront", installation_id: 7001 },
  { full_name: "yago/blog", private: false, default_branch: "main", clone_url: "https://github.com/yago/blog.git", source: "github:yago/blog", installation_id: 7002 },
];

const BRANCHES = {
  items: [
    { name: "production", protected: true, commit: "4f2a9c1" },
    { name: "staging", protected: false, commit: "9b1e22d" },
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

const JOB = {
  id: "0dd5e7a1",
  type: "deploy",
  name: "Deploy storefront.example.com",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Deploying",
  created_at: "2026-09-25T19:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "storefront.example.com" },
};

function wizard(status: GitHubStatus | null, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    ...(status !== null ? { "GET /api/integrations/github": () => json(200, status) } : {}),
    "GET /api/integrations/github/repositories": () => json(200, { items: REPOSITORIES, total: REPOSITORIES.length }),
    "GET /api/integrations/github/repositories/acme/storefront/branches": () => json(200, BRANCHES),
    "POST /api/apps/inspect": () => json(200, INSPECTION),
    "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Deployment queued", job: JOB }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [JOB], total: 1, active: 1 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

describe("filterRepositories", () => {
  it("puts repositories whose name starts with the text first, then the rest that hold it", () => {
    const names = (query: string) => filterRepositories(REPOSITORIES, query).map((repository) => repository.full_name);
    expect(names("")).toEqual(["acme/api", "acme/storefront", "yago/blog"]);
    expect(names("sto")).toEqual(["acme/storefront"]);
    expect(names("acme")).toEqual(["acme/api", "acme/storefront"]);
    expect(names("b")).toEqual(["yago/blog"]);
    expect(names("zzz")).toEqual([]);
  });
});

describe("the new-app wizard, from GitHub", () => {
  it("offers the App's repositories first, searchable, private ones marked, and passes axe", { timeout: 20_000 }, async () => {
    const { user } = wizard(STATUS).harness;
    const search = await screen.findByRole("combobox", { name: "Repository" });
    expect(screen.getByRole("radio", { name: "From GitHub" })).toBeChecked();
    const list = screen.getByRole("listbox", { name: "Repositories" });
    const options = within(list).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      expect.stringContaining("acme/api"),
      expect.stringContaining("acme/storefront"),
      expect.stringContaining("yago/blog"),
    ]);
    const [api, , blog] = options;
    if (!api || !blog) throw new Error("missing options");
    expect(within(api).getByText("Private")).toBeInTheDocument();
    expect(within(blog).getByText("Public")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);

    await user.type(search, "blog");
    expect(within(list).getAllByRole("option")).toHaveLength(1);
    await user.clear(search);
    await user.type(search, "nothing-like-it");
    expect(await screen.findByText('No repository matches "nothing-like-it".')).toBeInTheDocument();
  });

  it("chooses a repository from the keyboard, preselects its default branch, and carries the installation into the inspection and the deploy", { timeout: 30_000 }, async () => {
    const { backend, harness } = wizard(STATUS);
    const { user } = harness;
    const search = await screen.findByRole("combobox", { name: "Repository" });
    await user.click(search);
    await user.keyboard("{ArrowDown}");
    expect(search).toHaveAttribute("aria-activedescendant", within(screen.getByRole("listbox")).getAllByRole("option")[1]?.id);
    await user.keyboard("{Enter}");

    // Chosen: the repository stands in place of the search, focus on its control.
    expect(await screen.findByRole("button", { name: "Change repository" })).toHaveFocus();
    expect(screen.getByText("acme/storefront")).toBeInTheDocument();
    const branch = screen.getByRole("combobox", { name: "Branch" });
    expect(branch).toHaveTextContent("production");

    await user.click(branch);
    await user.click(await screen.findByRole("option", { name: "staging" }));
    expect(branch).toHaveTextContent("staging");
    await expectNoAxeViolations(document.body);

    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    expect(backend.callsTo("POST /api/apps/inspect").at(-1)?.body).toEqual({
      source: "github:acme/storefront",
      branch: "staging",
      github_installation_id: 7001,
    });

    await user.type(screen.getByLabelText("Domain"), "storefront.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Variables" });
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy storefront.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({
      domain: "storefront.example.com",
      source: "github:acme/storefront",
      branch: "staging",
      github_installation_id: 7001,
    });
  });

  it("asks for a repository before inspecting", async () => {
    const { backend, harness } = wizard(STATUS);
    await screen.findByRole("combobox", { name: "Repository" });
    await harness.user.click(screen.getByRole("button", { name: "Inspect source" }));
    // Said by Continue, and on the picker it points at.
    expect(await screen.findAllByText("Choose a repository.")).toHaveLength(2);
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(0);
  });

  it("changes the repository, and Escape keeps the one chosen", async () => {
    const { user } = wizard(STATUS).harness;
    await user.click(await screen.findByRole("option", { name: /yago\/blog/ }));
    await user.click(await screen.findByRole("button", { name: "Change repository" }));
    const search = screen.getByRole("combobox", { name: "Repository" });
    expect(search).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(await screen.findByText("yago/blog")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Repository" })).not.toBeInTheDocument();
  });

  it("keeps the typed source one choice away, without an installation", { timeout: 20_000 }, async () => {
    const { backend, harness } = wizard(STATUS);
    const { user } = harness;
    await user.click(await screen.findByRole("option", { name: /acme\/api/ }));
    await user.click(screen.getByRole("radio", { name: "URL or path" }));
    const field = screen.getByLabelText("Repository or directory");
    // Switching starts clean: what was chosen on GitHub does not linger as a typed source.
    expect(field).toHaveValue("");
    await user.type(field, "https://gitlab.com/acme/api.git");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    expect(backend.callsTo("POST /api/apps/inspect").at(-1)?.body).toEqual({ source: "https://gitlab.com/acme/api.git" });
  });

  it("types the branch when GitHub cannot list them", async () => {
    const { user } = wizard(STATUS, {
      "GET /api/integrations/github/repositories/acme/storefront/branches": () => problem(502, "integrationerror", "GitHub answered 502 Bad Gateway"),
    }).harness;
    await user.click(await screen.findByRole("option", { name: /acme\/storefront/ }));
    expect(await screen.findByText("GitHub answered 502 Bad Gateway")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Branch" })).toHaveValue("production");
  });

  it("points to installing the App when it has no installation", async () => {
    wizard({ ...STATUS, installations: [] });
    expect(await screen.findByText(/not installed on any account yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Install on GitHub/ })).toHaveAttribute("href", STATUS.install_url);
  });

  it("links to Settings > Integrations when no App is connected, and keeps the typed source", async () => {
    wizard({ configured: false, installations: [], hooks_url: null, hooks_active: false });
    expect(await screen.findByRole("link", { name: "Connect a GitHub App" })).toHaveAttribute("href", "/settings/integrations");
    expect(screen.getByLabelText("Repository or directory")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "From GitHub" })).not.toBeInTheDocument();
  });

  it("stays on the typed source when the integration cannot be read", async () => {
    const { backend } = wizard(null);
    expect(await screen.findByLabelText("Repository or directory")).toBeInTheDocument();
    await waitFor(() => {
      expect(backend.callsTo("GET /api/integrations/github")).toHaveLength(1);
    });
    expect(screen.queryByRole("radio", { name: "From GitHub" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Connect a GitHub App" })).not.toBeInTheDocument();
  });
});
