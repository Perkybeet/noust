import { configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { databaseRoutes } from "../databases/testFixtures";
import { DEFAULT_TEAM, ENVIRONMENTS, PROJECTS, teamsRoutes, viewerSession } from "./testFixtures";

function listAt(path: string, routes: Record<string, RouteHandler> = {}, options: Parameters<typeof teamsRoutes>[0] = {}, session?: Parameters<typeof teamsRoutes>[1]) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...databaseRoutes(),
    ...teamsRoutes(options, session),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    ...routes,
  });
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

describe("applications say where they live, and are filtered by it", () => {
  it("draws no column and no filter on a server nobody organised", async () => {
    listAt("/apps", {}, { teams: [DEFAULT_TEAM], projects: PROJECTS.slice(0, 1), environments: ENVIRONMENTS.slice(0, 1) });
    const table = await screen.findByRole("region", { name: /Applications/ });
    await within(table).findByRole("link", { name: "shop.example.com" });
    expect(within(table).queryByRole("columnheader", { name: "Project" })).not.toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Environment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Project" })).not.toBeInTheDocument();
  });

  it("draws no column and no filter on a server that has no teams to give", async () => {
    screenWidth(1440);
    fakeBackend({ ...signedInRoutes() });
    renderConsole("/apps");
    const table = await screen.findByRole("region", { name: /Applications/ });
    await within(table).findByRole("link", { name: "shop.example.com" });
    expect(within(table).queryByRole("columnheader", { name: "Project" })).not.toBeInTheDocument();
  });

  it("says the team, the project and the environment of each when every team is in view", async () => {
    listAt("/apps");
    const table = await screen.findByRole("region", { name: /Applications/ });
    // The columns arrive with the teams, a moment after the rows.
    await within(table).findByRole("columnheader", { name: "Project" });
    const shop = (await within(table).findByRole("link", { name: "shop.example.com" })).closest("tr");
    if (shop === null) throw new Error("no row");
    expect(within(shop).getByText("Web")).toBeInTheDocument();
    expect(within(shop).getByText("Acme Corp")).toBeInTheDocument();
    expect(within(shop).getByText("Production")).toBeInTheDocument();
    expect(screen.getByText("3 applications")).toBeInTheDocument();
  });

  it("filters by project, in the URL, and offers only the environments that project has", async () => {
    const { user, location } = listAt("/apps");
    const table = await screen.findByRole("region", { name: /Applications/ });
    await within(table).findByRole("link", { name: "shop.example.com" });
    await user.click(await screen.findByRole("combobox", { name: "Project" }));
    await user.click(await screen.findByRole("option", { name: "Web" }));
    await waitFor(() => {
      expect(location().search).toEqual({ project: "web" });
    });
    expect(within(table).queryByRole("link", { name: "admin.example.com" })).not.toBeInTheDocument();
    expect(screen.getByText("2 of 3 applications")).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Environment" }));
    await user.click(await screen.findByRole("option", { name: "Staging" }));
    await waitFor(() => {
      expect(location().search).toEqual({ project: "web", environment: "staging" });
    });
    expect(within(table).queryByRole("link", { name: "shop.example.com" })).not.toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "staging.example.com" })).toBeInTheDocument();
  });

  it("opens already filtered from a link", async () => {
    listAt("/apps?environment=staging");
    const table = await screen.findByRole("region", { name: /Applications/ });
    await within(table).findByRole("link", { name: "staging.example.com" });
    expect(within(table).queryByRole("link", { name: "shop.example.com" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("keeps the filters while another one is typed, and clears them together", async () => {
    const { user, location } = listAt("/apps?project=web");
    const table = await screen.findByRole("region", { name: /Applications/ });
    await within(table).findByRole("link", { name: "shop.example.com" });
    await user.type(screen.getByRole("searchbox", { name: "Search applications" }), "stag");
    await waitFor(() => {
      expect(location().search).toEqual({ project: "web", q: "stag" });
    });
    await user.click(screen.getAllByRole("button", { name: "Clear filters" })[0] ?? document.body);
    await waitFor(() => {
      expect(location().search).toEqual({});
    });
  });
});

describe("sites and databases say where they live too", () => {
  it("lists the sites with their project and environment, filtered by them", async () => {
    const { user, location } = listAt("/domains/sites");
    const table = await screen.findByRole("region", { name: /sites/i });
    expect(await within(table).findByRole("columnheader", { name: "Project" })).toBeInTheDocument();
    await user.click(await screen.findByRole("combobox", { name: "Project" }));
    await user.click(await screen.findByRole("option", { name: "Web" }));
    await waitFor(() => {
      expect(location().search).toEqual({ project: "web" });
    });
    expect(within(table).queryAllByText("admin.example.com")).toHaveLength(0);
    expect(within(table).getAllByText("shop.example.com").length).toBeGreaterThan(0);
  });

  it("lists the databases with their project and environment, filtered by them", async () => {
    const { user, location } = listAt("/databases");
    // The list waits for the policies and the open ports before it draws its rows, in a table of its own.
    await screen.findByText("shop_production", {}, { timeout: 4_000 });
    expect(within(screen.getByRole("region", { name: /Databases/ })).getByRole("columnheader", { name: "Project" })).toBeInTheDocument();
    await user.click(await screen.findByRole("combobox", { name: "Project" }));
    await user.click(await screen.findByRole("option", { name: "Web" }));
    await waitFor(() => {
      expect(location().search).toEqual({ project: "web" });
    });
    expect(screen.queryByText("admin_db")).not.toBeInTheDocument();
  });
});

describe("moving an application", () => {
  it("is offered in the row's menu, takes the sites and databases with it and says where it went", async () => {
    const { user, backend } = listAt(
      "/apps",
      {
        "POST /api/apps/shop.example.com/move": () => json(200, { domain: "shop.example.com", team: "acme", project: "web", environment: "staging", moved: { apps: 1, sites: 1, databases: 1 } }),
      },
      { active: "acme" },
    );
    await user.click(await screen.findByRole("button", { name: "Actions for shop.example.com" }));
    await user.click(await screen.findByRole("menuitem", { name: /Move to another project/ }));
    const dialog = await screen.findByRole("dialog", { name: "Move shop.example.com" });
    expect(within(dialog).getByText(/Now in/)).toHaveTextContent("acme / web / production");
    // Nothing to move until something else is chosen.
    expect(within(dialog).getByRole("button", { name: "Move application" })).toBeDisabled();
    await user.click(within(dialog).getByRole("combobox", { name: "Environment" }));
    await user.click(await screen.findByRole("option", { name: "Staging" }));
    await user.click(within(dialog).getByRole("button", { name: "Move application" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/shop.example.com/move")[0]?.body).toEqual({ team: "acme", project: "web", environment: "staging" });
    });
    expect(await screen.findByText(/Moved shop.example.com to Acme Corp \/ Web \/ Staging/)).toBeInTheDocument();
  });

  it("can cross teams, which the team in view does not otherwise offer", async () => {
    const { user, backend } = listAt(
      "/apps",
      { "POST /api/apps/shop.example.com/move": () => json(200, { domain: "shop.example.com", team: "default", project: "default", environment: "production", moved: { apps: 1 } }) },
      { active: "acme" },
    );
    await user.click(await screen.findByRole("button", { name: "Actions for shop.example.com" }));
    await user.click(await screen.findByRole("menuitem", { name: /Move to another project/ }));
    const dialog = await screen.findByRole("dialog", { name: "Move shop.example.com" });
    await user.click(within(dialog).getByRole("combobox", { name: "Team" }));
    await user.click(await screen.findByRole("option", { name: "Default" }));
    await user.click(within(dialog).getByRole("button", { name: "Move application" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/shop.example.com/move")[0]?.body).toEqual({ team: "default", project: "default", environment: "production" });
    });
  });

  it("shows the server's refusal in the dialog, which stays open", async () => {
    const { user } = listAt(
      "/apps",
      { "POST /api/apps/shop.example.com/move": () => problem(403, "permissiondenied", "You may not manage applications in acme", { hint: "Ask an owner." }) },
      { active: "acme" },
    );
    await user.click(await screen.findByRole("button", { name: "Actions for shop.example.com" }));
    await user.click(await screen.findByRole("menuitem", { name: /Move to another project/ }));
    const dialog = await screen.findByRole("dialog", { name: "Move shop.example.com" });
    await user.click(within(dialog).getByRole("combobox", { name: "Environment" }));
    await user.click(await screen.findByRole("option", { name: "Staging" }));
    await user.click(within(dialog).getByRole("button", { name: "Move application" }));
    expect(await within(dialog).findByText("You may not manage applications in acme")).toBeInTheDocument();
  });

  it("is not offered to someone who may not manage applications", async () => {
    const { user } = listAt("/apps", {}, { active: "acme" }, viewerSession());
    await user.click(await screen.findByRole("button", { name: "Actions for shop.example.com" }));
    await screen.findByRole("menuitem", { name: "Open" });
    expect(screen.queryByRole("menuitem", { name: /Move to another project/ })).not.toBeInTheDocument();
  });

  it("is not offered where there is nowhere else to go", async () => {
    const { user } = listAt("/apps", {}, { teams: [DEFAULT_TEAM], projects: PROJECTS.slice(0, 1), environments: ENVIRONMENTS.slice(0, 1) });
    await user.click(await screen.findByRole("button", { name: "Actions for shop.example.com" }));
    await screen.findByRole("menuitem", { name: "Open" });
    expect(screen.queryByRole("menuitem", { name: /Move to another project/ })).not.toBeInTheDocument();
  });

  it("says where an application lives in its settings, with Move beside it", async () => {
    const { user } = listAt("/apps/shop.example.com/settings", {
      "GET /api/apps/shop.example.com": () => json(200, { domain: "shop.example.com", name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "inplace", team: "acme", project: "web", environment: "production" }),
    });
    expect(await screen.findByText("Acme Corp / Web / Production")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Move" }));
    expect(await screen.findByRole("dialog", { name: "Move shop.example.com" })).toBeInTheDocument();
  });
});
