import { configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { readLocalActiveTeam } from "./activeTeam";
import { ACME, DEFAULT_TEAM, governingSession, organisation, teamsRoutes } from "./testFixtures";

const ACCOUNT_SESSION = governingSession();

async function shellAt(routes: Record<string, RouteHandler>, path = "/") {
  screenWidth(1440);
  const backend = fakeBackend(routes);
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
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

describe("the team switcher", () => {
  it("is not drawn on a server nobody organised: one team called default has nothing to switch", async () => {
    await shellAt({ ...signedInRoutes(), ...organisation({ teams: [DEFAULT_TEAM], projects: [], environments: [] }) });
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(screen.queryByTestId("team-switcher")).not.toBeInTheDocument();
  });

  it("is not drawn, and nothing breaks, on a server that has no teams to give", async () => {
    await shellAt(signedInRoutes());
    expect(screen.queryByTestId("team-switcher")).not.toBeInTheDocument();
  });

  it("names the team in view, or says every team is", async () => {
    await shellAt({ ...teamsRoutes({ active: "acme" }, ACCOUNT_SESSION) });
    expect(await screen.findByRole("button", { name: "Team: Acme Corp" })).toBeInTheDocument();
  });

  it("says All teams for an administrator that has chosen none", async () => {
    await shellAt({ ...teamsRoutes({ active: null }, ACCOUNT_SESSION) });
    expect(await screen.findByRole("button", { name: "Team: All teams" })).toBeInTheDocument();
  });

  it("says All my teams to someone who does not see every team", async () => {
    await shellAt({ ...teamsRoutes({ active: null, seesAll: false, teams: [ACME, { ...DEFAULT_TEAM, role: "viewer" }] }, ACCOUNT_SESSION) });
    expect(await screen.findByRole("button", { name: "Team: All my teams" })).toBeInTheDocument();
  });

  it("changes the team through the account, and says so", async () => {
    const { user, backend } = await shellAt({
      ...teamsRoutes({ active: null }, ACCOUNT_SESSION),
      "PUT /api/teams/active": () => json(200, { teams: [DEFAULT_TEAM, ACME], active: "acme", sees_all: true }),
    });
    await user.click(await screen.findByRole("button", { name: "Team: All teams" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitemradio")).toHaveLength(3);
    await user.click(within(menu).getByRole("menuitemradio", { name: /Acme Corp/ }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/teams/active")[0]?.body).toEqual({ team: "acme" });
    });
    expect(await screen.findByRole("button", { name: "Team: Acme Corp" })).toBeInTheDocument();
  });

  it("goes back to every team with the same menu", async () => {
    const { user, backend } = await shellAt({
      ...teamsRoutes({ active: "acme" }, ACCOUNT_SESSION),
      "PUT /api/teams/active": () => json(200, { teams: [DEFAULT_TEAM, ACME], active: null, sees_all: true }),
    });
    await user.click(await screen.findByRole("button", { name: "Team: Acme Corp" }));
    await user.click(await screen.findByRole("menuitemradio", { name: /All teams/ }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/teams/active")[0]?.body).toEqual({ team: null });
    });
  });

  it("remembers the choice in the browser for the access token, which has no account to remember it", async () => {
    const { user, backend } = await shellAt({ ...teamsRoutes({ active: null }, { ...SESSION, permissions: [...(SESSION.permissions ?? []), "teams.read", "teams.manage"] }) });
    await user.click(await screen.findByRole("button", { name: "Team: All teams" }));
    await user.click(await screen.findByRole("menuitemradio", { name: /Acme Corp/ }));
    await waitFor(() => {
      expect(readLocalActiveTeam(null)).toBe("acme");
    });
    expect(backend.callsTo("PUT /api/teams/active")).toHaveLength(0);
    expect(await screen.findByRole("button", { name: "Team: Acme Corp" })).toBeInTheDocument();
  });

  it("says the server's own words when the change is refused", async () => {
    const { user } = await shellAt({
      ...teamsRoutes({ active: null }, ACCOUNT_SESSION),
      "PUT /api/teams/active": () => problem(404, "teamnotfounderror", "No team named 'acme'", { hint: "List the teams with GET /api/teams/mine." }),
    });
    await user.click(await screen.findByRole("button", { name: "Team: All teams" }));
    await user.click(await screen.findByRole("menuitemradio", { name: /Acme Corp/ }));
    expect(await screen.findByText("Could not change team")).toBeInTheDocument();
    expect(screen.getByText("No team named 'acme'")).toBeInTheDocument();
  });

  it("opens the team's settings and the list of teams from its menu", async () => {
    const { user, location } = await shellAt({ ...teamsRoutes({ active: "acme" }, ACCOUNT_SESSION) });
    await user.click(await screen.findByRole("button", { name: "Team: Acme Corp" }));
    await user.click(await screen.findByRole("menuitem", { name: "Settings of Acme Corp" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/teams/acme");
    });
  });

  it("narrows the applications list to the team in view, and says where each one lives", async () => {
    await shellAt({ ...teamsRoutes({ active: "acme" }, ACCOUNT_SESSION) }, "/apps");
    const table = await screen.findByRole("table", { name: "Applications" });
    await within(table).findByRole("link", { name: "shop.example.com" });
    expect(within(table).getByRole("link", { name: "staging.example.com" })).toBeInTheDocument();
    expect(within(table).queryByRole("link", { name: "admin.example.com" })).not.toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Project" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Environment" })).toBeInTheDocument();
    expect(screen.getByText("2 applications")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = await shellAt({ ...teamsRoutes({ active: "acme" }, ACCOUNT_SESSION) });
    await screen.findByRole("button", { name: "Team: Acme Corp" });
    await expectNoAxeViolations(container, { page: true });
  });
});
