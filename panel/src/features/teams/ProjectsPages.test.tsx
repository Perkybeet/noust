import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { ENVIRONMENTS, PROJECTS, teamsRoutes, viewerSession } from "./testFixtures";

function projectsAt(path = "/projects", routes: Record<string, RouteHandler> = {}, options: Parameters<typeof teamsRoutes>[0] = {}, session?: Parameters<typeof teamsRoutes>[1]) {
  screenWidth(1440);
  const backend = fakeBackend({ ...teamsRoutes(options, session), ...routes });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

// The organisation is read in three steps (the teams, then their projects and environments, then
// the lists), which a busy machine stretches past the second a lookup waits by default.
// A test's own budget as well, from its first line: a hook that sets it applies from the next test.
vi.setConfig({ testTimeout: 20_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

describe("the projects list", () => {
  it("lists every project, its team beside it when several are in view, and what each holds", { timeout: 15_000 }, async () => {
    const { container } = projectsAt();
    await screen.findByRole("heading", { level: 1, name: "Projects" });
    const web = (await screen.findByRole("link", { name: "Web" })).closest("tr");
    if (web === null) throw new Error("no row for Web");
    expect(within(web).getByText("Acme Corp")).toBeInTheDocument();
    expect(within(web).getByText("Production, Staging")).toBeInTheDocument();
    // Web holds the shop (and its site and its database) in production and the staging app.
    await waitFor(() => {
      const cells = within(web).getAllByRole("cell").map((cell) => cell.textContent);
      expect(cells).toContain("2");
    });
    expect(screen.getByText("3 projects")).toHaveAttribute("role", "status");
    await expectNoAxeViolations(container);
  });

  it("is the team's own when the operator works in one", async () => {
    projectsAt("/projects", {}, { active: "acme" });
    await screen.findByRole("link", { name: "Web" });
    expect(screen.getAllByRole("link", { name: /^(Default|Web)$/ })).toHaveLength(2);
    expect(screen.queryByRole("columnheader", { name: "Team" })).not.toBeInTheDocument();
  });

  it("makes a project in two clicks and opens it", async () => {
    const { user, backend, location } = projectsAt("/projects", {
      "POST /api/teams/acme/projects": () => json(201, { id: 9, team: "acme", slug: "mobile", name: "Mobile", environments: ["production"], created_at: 1 }),
    }, { active: "acme" });
    await user.click(await screen.findByRole("button", { name: "New project" }));
    const dialog = await screen.findByRole("dialog", { name: "New project" });
    await user.type(within(dialog).getByLabelText("Project name"), "Mobile");
    await user.click(within(dialog).getByRole("button", { name: "Create project" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/teams/acme/projects")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/teams/acme/projects")[0]?.body).toEqual({ name: "Mobile" });
    await waitFor(() => {
      expect(location().pathname).toBe("/projects/acme/mobile");
    });
  });

  it("sends the short name when one is typed, and says the server's refusal where it was typed", async () => {
    const { user, backend } = projectsAt("/projects", {
      "POST /api/teams/acme/projects": () => problem(409, "teamconflicterror", "The team 'acme' already has a project named 'web'", { hint: "Pick another slug with --slug.", fields: { slug: "The team 'acme' already has a project named 'web'" } }),
    }, { active: "acme" });
    await user.click(await screen.findByRole("button", { name: "New project" }));
    const dialog = await screen.findByRole("dialog", { name: "New project" });
    await user.type(within(dialog).getByLabelText("Project name"), "Web again");
    await user.type(within(dialog).getByLabelText(/Short name/), "web");
    await user.click(within(dialog).getByRole("button", { name: "Create project" }));
    expect(await within(dialog).findByText("The team 'acme' already has a project named 'web'")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/teams/acme/projects")[0]?.body).toEqual({ name: "Web again", slug: "web" });
  });

  it("opens the new-project dialog from the palette's link", async () => {
    projectsAt("/projects?new=true", {}, { active: "acme" });
    expect(await screen.findByRole("dialog", { name: "New project" })).toBeInTheDocument();
  });

  it("offers no way to create or change anything to someone who only reads", async () => {
    const { user } = projectsAt("/projects", {}, {}, viewerSession());
    await screen.findByRole("link", { name: "Web" });
    expect(screen.queryByRole("button", { name: "New project" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions for Web" }));
    expect(await screen.findByRole("menuitem", { name: "Open" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Rename project" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Delete project" })).not.toBeInTheDocument();
  });

  it("renames a project from its row and keeps the short name", async () => {
    const { user, backend } = projectsAt("/projects", {
      "PATCH /api/teams/acme/projects/web": () => json(200, { id: 3, team: "acme", slug: "web", name: "Website", environments: ["production", "staging"], created_at: 1 }),
    }, { active: "acme" });
    await user.click(await screen.findByRole("button", { name: "Actions for Web" }));
    await user.click(await screen.findByRole("menuitem", { name: "Rename project" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename Web" });
    const name = within(dialog).getByLabelText("Project name");
    await user.clear(name);
    await user.type(name, "Website");
    await user.click(within(dialog).getByRole("button", { name: "Save name" }));
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/teams/acme/projects/web")).toHaveLength(1);
    });
    expect(backend.callsTo("PATCH /api/teams/acme/projects/web")[0]?.body).toEqual({ name: "Website" });
  });

  it("will not delete a project that holds something, and says what it holds", async () => {
    const { user } = projectsAt("/projects", {}, { active: "acme" });
    const row = (await screen.findByRole("link", { name: "Web" })).closest("tr");
    if (row === null) throw new Error("no row");
    // Counts arrive with the lists: the guard is drawn from them.
    await waitFor(() => {
      expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toContain("2");
    });
    await user.click(within(row).getByRole("button", { name: "Actions for Web" }));
    const remove = await screen.findByRole("menuitem", { name: /Delete project/ });
    expect(remove).toHaveAttribute("aria-disabled", "true");
    expect(remove).toHaveAccessibleDescription(/still holds 4 resources/);
  });

  it("will not delete the default project either", async () => {
    const { user } = projectsAt("/projects", {}, { active: "default" });
    await user.click(await screen.findByRole("button", { name: "Actions for Default" }));
    const remove = await screen.findByRole("menuitem", { name: /Delete project/ });
    expect(remove).toHaveAttribute("aria-disabled", "true");
    expect(remove).toHaveAccessibleDescription(/receives what nobody assigned/);
  });

  it("deletes an empty project after one question", async () => {
    const empty = { id: 7, team: "acme", slug: "sandbox", name: "Sandbox", environments: ["production"], created_at: 1 };
    const { user, backend } = projectsAt("/projects", {
      "DELETE /api/teams/acme/projects/sandbox": () => json(200, { message: "Deleted the project acme/sandbox" }),
    }, { active: "acme", projects: [...PROJECTS, empty], environments: [...ENVIRONMENTS, { id: 9, team: "acme", project: "sandbox", slug: "production", name: "Production", created_at: 1 }] });
    await user.click(await screen.findByRole("button", { name: "Actions for Sandbox" }));
    await user.click(await screen.findByRole("menuitem", { name: /Delete project/ }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Sandbox" });
    await user.click(within(dialog).getByRole("button", { name: "Delete project" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/teams/acme/projects/sandbox")).toHaveLength(1);
    });
  });

  it("says there are no projects when there are none, with the way to make one", async () => {
    projectsAt("/projects", {}, { projects: [], environments: [] });
    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    // The header's and the empty state's: one place each, never two in a row.
    expect(screen.getAllByRole("button", { name: "New project" })).toHaveLength(2);
  });

  it("says why it cannot show the projects, with the server's words and a way to try again", async () => {
    projectsAt("/projects", { "GET /api/teams/mine": () => problem(403, "forbidden", "Teams are not for you", {}) });
    expect(await screen.findByText("Could not load the teams")).toBeInTheDocument();
    expect(screen.getByText("Teams are not for you")).toBeInTheDocument();
  });

  it("is in Spanish", async () => {
    await act(() => setLocale("es"));
    projectsAt("/projects", {}, { active: "acme" });
    expect(await screen.findByRole("heading", { level: 1, name: "Proyectos" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Nuevo proyecto" })).toBeInTheDocument();
    expect(await screen.findByRole("columnheader", { name: "Entornos" })).toBeInTheDocument();
  });
});

describe("a project's page", () => {
  it("shows its environments, production first, and what lives in the one on screen", async () => {
    const { container } = projectsAt("/projects/acme/web");
    await screen.findByRole("heading", { level: 1, name: "Web" });
    const environments = screen.getByRole("radiogroup", { name: "Environment" });
    expect(within(environments).getAllByRole("radio").map((radio) => radio.getAttribute("aria-label") ?? radio.textContent)).toEqual(["Production", "Staging"]);
    expect(within(environments).getByRole("radio", { name: "Production" })).toBeChecked();
    // The application, its database and its site, each in a section of its own kind.
    const applications = await screen.findByRole("region", { name: "Applications" });
    expect(await within(applications).findByRole("link", { name: "shop.example.com" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Databases" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "shop_production" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sites" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "staging.example.com" })).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("changes environment in the URL, one click", async () => {
    const { user, location } = projectsAt("/projects/acme/web");
    const applications = await screen.findByRole("region", { name: "Applications" });
    await within(applications).findByRole("link", { name: "shop.example.com" });
    await user.click(screen.getByRole("radio", { name: "Staging" }));
    await waitFor(() => {
      expect(location().search).toEqual({ env: "staging" });
    });
    expect(await within(applications).findByRole("link", { name: "staging.example.com" })).toBeInTheDocument();
    expect(within(applications).queryByRole("link", { name: "shop.example.com" })).not.toBeInTheDocument();
  });

  it("starts a new application in this project and environment", async () => {
    projectsAt("/projects/acme/web?env=staging");
    const link = await screen.findByRole("link", { name: "New application" });
    const href = link.getAttribute("href") ?? "";
    expect(href).toContain("/apps/new");
    expect(href).toContain("project=web");
    expect(href).toContain("environment=staging");
    expect(href).toContain("team=acme");
  });

  it("makes an environment in two clicks and shows it", async () => {
    const { user, backend, location } = projectsAt("/projects/acme/web", {
      "POST /api/teams/acme/projects/web/environments": () => json(201, { id: 9, team: "acme", project: "web", slug: "preview", name: "Preview", created_at: 1 }),
    });
    await user.click(await screen.findByRole("button", { name: "New environment" }));
    const dialog = await screen.findByRole("dialog", { name: "New environment in Web" });
    await user.type(within(dialog).getByLabelText("Environment name"), "Preview");
    await user.click(within(dialog).getByRole("button", { name: "Create environment" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/teams/acme/projects/web/environments")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/teams/acme/projects/web/environments")[0]?.body).toEqual({ name: "Preview" });
    await waitFor(() => {
      expect(location().search).toEqual({ env: "preview" });
    });
  });

  it("will not delete an environment that holds something, nor a project's last one", async () => {
    const { user } = projectsAt("/projects/acme/default");
    await screen.findByRole("heading", { level: 1, name: "Default" });
    await user.click(await screen.findByRole("button", { name: "Actions for the environment Production" }));
    const remove = await screen.findByRole("menuitem", { name: /Delete environment/ });
    expect(remove).toHaveAttribute("aria-disabled", "true");
    expect(remove).toHaveAccessibleDescription(/keeps at least one environment/);
  });

  it("deletes an empty environment after one question", async () => {
    const { user, backend } = projectsAt("/projects/acme/web?env=staging", {
      "DELETE /api/teams/acme/projects/web/environments/staging": () => json(200, { message: "Deleted" }),
      // Nothing in staging.
      "GET /api/apps": () => json(200, { total: 0, apps: [] }),
      "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
      "GET /api/databases/databases": () => json(200, { databases: [], total: 0, problems: [] }),
    });
    await screen.findByText("Staging is empty");
    await user.click(await screen.findByRole("button", { name: "Actions for the environment Staging" }));
    await user.click(await screen.findByRole("menuitem", { name: /Delete environment/ }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Staging" });
    await user.click(within(dialog).getByRole("button", { name: "Delete environment" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/teams/acme/projects/web/environments/staging")).toHaveLength(1);
    });
  });

  it("renames an environment and keeps its short name", async () => {
    const { user, backend } = projectsAt("/projects/acme/web?env=staging", {
      "PATCH /api/teams/acme/projects/web/environments/staging": () => json(200, { id: 4, team: "acme", project: "web", slug: "staging", name: "Pre-production", created_at: 1 }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for the environment Staging" }));
    await user.click(await screen.findByRole("menuitem", { name: "Rename environment" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename Staging" });
    const name = within(dialog).getByLabelText("Environment name");
    await user.clear(name);
    await user.type(name, "Pre-production");
    await user.click(within(dialog).getByRole("button", { name: "Save name" }));
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/teams/acme/projects/web/environments/staging")[0]?.body).toEqual({ name: "Pre-production" });
    });
  });

  it("says an empty environment is empty and what to do, with the command", async () => {
    projectsAt("/projects/acme/web", {
      "GET /api/apps": () => json(200, { total: 0, apps: [] }),
      "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
      "GET /api/databases/databases": () => json(200, { databases: [], total: 0, problems: [] }),
    });
    expect(await screen.findByText("Production is empty")).toBeInTheDocument();
    expect(screen.getByText(/--team acme --project web --environment production/)).toBeInTheDocument();
  });

  it("says a project that is not there is not there", async () => {
    projectsAt("/projects/acme/nowhere");
    expect(await screen.findByText("Project not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All projects" })).toBeInTheDocument();
  });

  it("hides what a reader may not do: no new application, no new environment, no menu of the environment", async () => {
    projectsAt("/projects/acme/web", {}, {}, viewerSession());
    await screen.findByRole("heading", { level: 1, name: "Web" });
    await within(await screen.findByRole("region", { name: "Applications" })).findByRole("link", { name: "shop.example.com" });
    expect(screen.queryByRole("link", { name: "New application" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New environment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Actions for the environment/ })).not.toBeInTheDocument();
  });

  it("is in Spanish", async () => {
    await act(() => setLocale("es"));
    projectsAt("/projects/acme/web");
    expect(await screen.findByRole("radiogroup", { name: "Entorno" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Nuevo entorno" })).toBeInTheDocument();
  });
});
