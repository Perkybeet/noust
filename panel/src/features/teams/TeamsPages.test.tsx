import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { ACME, DEFAULT_TEAM, teamsRoutes, viewerSession } from "./testFixtures";

const MEMBERS = [
  { username: "carla", role: "owner", instance_role: "viewer", created_at: 1_790_100_100, created_by: "ana" },
  { username: "alice", role: "admin", instance_role: "operator", created_at: 1_790_100_200, created_by: "carla" },
  { username: "omar", role: "viewer", instance_role: "viewer", created_at: 1_790_100_300, created_by: "carla" },
];

function teamAt(path: string, routes: Record<string, RouteHandler> = {}, options: Parameters<typeof teamsRoutes>[0] = {}, session?: Parameters<typeof teamsRoutes>[1]) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...teamsRoutes(options, session),
    "GET /api/teams/acme/members": () => json(200, { team: "acme", members: MEMBERS }),
    "GET /api/teams/default/members": () => json(200, { team: "default", members: [] }),
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

describe("the teams list", () => {
  it("lists the teams with the operator's role and what each holds, and says a team is not a machine", async () => {
    const { container } = teamAt("/teams");
    // Teams are the second tab of Projects: the same title, and the tab says where this is.
    await screen.findByRole("heading", { level: 1, name: "Projects" });
    const table = await screen.findByRole("table", { name: "Teams on this server" });
    const row = (await within(table).findByRole("link", { name: "Acme Corp" })).closest("tr");
    if (row === null) throw new Error("no row for Acme Corp");
    expect(within(row).getByText("Owner")).toBeInTheDocument();
    expect(await screen.findByText("A team is not a separate machine")).toBeInTheDocument();
    await waitFor(() => {
      expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toContain("2");
    });
    await expectNoAxeViolations(container);
  });

  it("makes a team in two clicks and goes to its members", async () => {
    const { user, backend, location } = teamAt("/teams", {
      "POST /api/teams": () => json(201, { id: 5, slug: "globex", name: "Globex", role: null, created_at: 1 }),
      "GET /api/teams/globex": () => json(200, { id: 5, slug: "globex", name: "Globex", role: null, created_at: 1, resources: {}, members: 0, projects: 1 }),
      "GET /api/teams/globex/members": () => json(200, { team: "globex", members: [] }),
    });
    await user.click(await screen.findByRole("button", { name: "New team" }));
    const dialog = await screen.findByRole("dialog", { name: "New team" });
    await user.type(within(dialog).getByLabelText("Team name"), "Globex");
    await user.click(within(dialog).getByRole("button", { name: "Create team" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/teams")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/teams")[0]?.body).toEqual({ name: "Globex" });
    await waitFor(() => {
      expect(location().pathname).toBe("/teams/globex/members");
    });
  });

  it("offers no New team to whoever does not see every team, though it owns one", async () => {
    teamAt("/teams", {}, { teams: [{ ...ACME, role: "owner" }], seesAll: false });
    await screen.findByRole("link", { name: "Acme Corp" });
    expect(screen.queryByRole("button", { name: "New team" })).not.toBeInTheDocument();
  });

  it("switches the team the operator works in from a row", async () => {
    const { user, backend } = teamAt("/teams", {
      "PUT /api/teams/active": () => json(200, { teams: [DEFAULT_TEAM, ACME], active: "acme", sees_all: true }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for Acme Corp" }));
    await user.click(await screen.findByRole("menuitem", { name: "Work in this team" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/teams/active")[0]?.body).toEqual({ team: "acme" });
    });
  });

  it("is in Spanish", async () => {
    await act(() => setLocale("es"));
    teamAt("/teams");
    expect(await screen.findByRole("heading", { level: 1, name: "Proyectos" })).toBeInTheDocument();
    expect(await screen.findByText("Un equipo no es una máquina aparte")).toBeInTheDocument();
  });
});

describe("a team's page", () => {
  it("says what the team is and holds, and its projects", async () => {
    const { container } = teamAt("/teams/acme");
    await screen.findByRole("heading", { level: 1, name: "Acme Corp" });
    expect(screen.getByRole("navigation", { name: "Team sections" })).toBeInTheDocument();
    expect(await screen.findByText("2 members")).toBeInTheDocument();
    expect(screen.getByText(/Applications 2 · sites 1 · databases 1/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Web" })).toHaveAttribute("href", "/projects/acme/web");
    await expectNoAxeViolations(container);
  });

  it("is not found for a team nobody sees, as the server answers it", async () => {
    teamAt("/teams/globex", { "GET /api/teams/globex": () => problem(404, "teamnotfounderror", "No team named 'globex'", {}) });
    expect(await screen.findByText("Team not found")).toBeInTheDocument();
  });

  it("lists the members, strongest first, with the server's role beside the team's", async () => {
    const { container } = teamAt("/teams/acme/members");
    const carla = await screen.findByText("carla");
    const first = screen.getAllByRole("row")[1];
    expect(first).toContainElement(carla);
    expect(await screen.findByText("3 members")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("changes a role in place", async () => {
    const { user, backend } = teamAt("/teams/acme/members", {
      "PATCH /api/teams/acme/members/omar": () => json(200, { ...MEMBERS[2], role: "operator" }),
    });
    await user.click(await screen.findByRole("combobox", { name: "Role of omar" }));
    await user.click(await screen.findByRole("option", { name: /Operator/ }));
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/teams/acme/members/omar")[0]?.body).toEqual({ role: "operator" });
    });
  });

  it("adds an account that exists, with its role", async () => {
    const { user, backend } = teamAt("/teams/acme/members", {
      "POST /api/teams/acme/members": () => json(201, { username: "dana", role: "operator", instance_role: "viewer", created_at: 1 }),
    });
    await user.click(await screen.findByRole("button", { name: "Add member" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a member to Acme Corp" });
    await user.type(within(dialog).getByLabelText("Account"), "dana");
    await user.click(within(dialog).getByRole("radio", { name: /Operator/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add member" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/teams/acme/members")[0]?.body).toEqual({ username: "dana", role: "operator" });
    });
  });

  it("says where an account that does not exist was typed", async () => {
    const { user } = teamAt("/teams/acme/members", {
      "POST /api/teams/acme/members": () => problem(404, "teamnotfounderror", "No account named 'ghost'", { hint: "Invite the person first.", fields: { username: "No account named 'ghost'" } }),
    });
    await user.click(await screen.findByRole("button", { name: "Add member" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a member to Acme Corp" });
    await user.type(within(dialog).getByLabelText("Account"), "ghost");
    await user.click(within(dialog).getByRole("button", { name: "Add member" }));
    expect(await within(dialog).findByText("No account named 'ghost'")).toBeInTheDocument();
  });

  it("removes a person after one question, and keeps the last owner", async () => {
    const { user, backend } = teamAt("/teams/acme/members", {
      "DELETE /api/teams/acme/members/omar": () => json(200, { message: "omar left the team acme" }),
    });
    await screen.findByText("carla");
    await user.click(await screen.findByRole("button", { name: "Actions for carla" }));
    const keep = await screen.findByRole("menuitem", { name: /Remove from team/ });
    expect(keep).toHaveAttribute("aria-disabled", "true");
    expect(keep).toHaveAccessibleDescription(/at least one owner/);
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Actions for omar" }));
    await user.click(await screen.findByRole("menuitem", { name: /Remove from team/ }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove omar from Acme Corp" });
    await user.click(within(dialog).getByRole("button", { name: "Remove member" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/teams/acme/members/omar")).toHaveLength(1);
    });
  });

  it("invites a person into the team through the accounts' invitation, with their role in it", async () => {
    const { user, backend } = teamAt("/teams/acme/members", {
      "POST /api/auth/invitations": () =>
        json(201, { code: "wasm_inv_abc", expires_in: 86_400, account: { username: "pablo", role: "viewer" } }),
    });
    await user.click(await screen.findByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person to Acme Corp" });
    await user.type(within(dialog).getByLabelText(/Username/), "pablo");
    await user.click(within(dialog).getByRole("radio", { name: /Operator/ }));
    await user.click(within(dialog).getByRole("button", { name: /Invite|Create invitation|Send/ }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/auth/invitations")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/auth/invitations")[0]?.body).toMatchObject({ username: "pablo", role: "viewer", team: "acme", team_role: "operator" });
  });

  it("does not offer to invite when the operator may not create accounts", async () => {
    teamAt("/teams/acme/members", {}, {}, { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "accounts.manage").concat(["teams.read", "teams.manage"]) });
    await screen.findByRole("button", { name: "Add member" });
    expect(screen.queryByRole("button", { name: "Invite a person" })).not.toBeInTheDocument();
  });

  it("is a read-only list for someone who does not govern the team", async () => {
    teamAt("/teams/acme/members", {}, { teams: [{ ...ACME, role: "viewer" }] }, viewerSession());
    await screen.findByText("carla");
    expect(screen.queryByRole("button", { name: "Add member" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Role of omar" })).not.toBeInTheDocument();
    expect(screen.getByText("You can read this list")).toBeInTheDocument();
  });

  it("says no one is in a team that has no members, with the command", async () => {
    teamAt("/teams/default/members", {}, { teams: [DEFAULT_TEAM, ACME] });
    expect(await screen.findByText("No one is in this team yet")).toBeInTheDocument();
  });

  it("renames the team from a save bar, keeping the short name", async () => {
    const { user, backend } = teamAt("/teams/acme/settings", {
      "PATCH /api/teams/acme": () => json(200, { ...ACME, name: "Acme Inc" }),
    });
    const name = await screen.findByLabelText("Team name");
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
    await user.clear(name);
    await user.type(name, "Acme Inc");
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/teams/acme")[0]?.body).toEqual({ name: "Acme Inc" });
    });
  });

  it("deletes an empty team after the short name is typed", async () => {
    const { user, backend, location } = teamAt("/teams/globex/settings", {
      "GET /api/teams/globex": () => json(200, { id: 5, slug: "globex", name: "Globex", role: "owner", created_at: 1, resources: { apps: 0, sites: 0, databases: 0, backup_schedules: 0 }, members: 1, projects: 1 }),
      "DELETE /api/teams/globex": () => json(200, { message: "Deleted the team globex" }),
    }, { teams: [DEFAULT_TEAM, ACME, { id: 5, slug: "globex", name: "Globex", role: "owner", created_at: 1 }] });
    await user.click(await screen.findByRole("button", { name: "Delete team" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Globex" });
    const confirm = within(dialog).getByRole("button", { name: "Delete team" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "globex");
    await user.click(confirm);
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/teams/globex")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(location().pathname).toBe("/projects/teams");
    });
  });

  it("will not delete a team that holds something, and says what", async () => {
    teamAt("/teams/acme/settings");
    const button = await screen.findByRole("button", { name: "Delete team" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/Move or delete what it holds first: applications 2, sites 1, databases 1/)).toBeInTheDocument();
  });

  it("will not delete the default team", async () => {
    teamAt("/teams/default/settings");
    expect(await screen.findByText("The default team receives what nobody assigned. It stays.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete team" })).toBeDisabled();
  });

  it("leaves the settings to read for someone who does not govern the team", async () => {
    teamAt("/teams/acme/settings", {}, { teams: [{ ...ACME, role: "viewer" }] }, viewerSession());
    const name = await screen.findByLabelText("Team name");
    expect(name).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Delete team" })).not.toBeInTheDocument();
  });
});
