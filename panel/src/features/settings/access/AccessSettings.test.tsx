import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { organisation } from "../../teams/testFixtures";
import { groupPermissions, roleScopes } from "./accessModel";

const NARROW = ["apps.read", "apps.operate", "apps.deploy", "apps.manage", "databases.read", "backups.read"];
const TEAM = [...NARROW, "teams.manage"];

const PERMISSIONS = {
  "apps.read": "See applications and their state",
  "apps.operate": "Restart, stop and start applications",
  "apps.deploy": "Deploy, update and roll back applications",
  "apps.manage": "Create and delete applications",
  "databases.read": "See databases",
  "backups.read": "See backups",
  "teams.manage": "Change a team's members",
  "accounts.manage": "Create accounts and change their roles",
  "server.manage": "Change the server's settings",
};

function role(name: string, label: string, permissions: string[], built_in: boolean, scopes: string[], description = "") {
  return { name, label, description, permissions: ["self", ...permissions], built_in, kind: built_in ? "team" : "custom", scopes };
}

const ROLES = [
  role("viewer", "Viewer", ["apps.read", "databases.read", "backups.read"], true, ["team", "project", "environment"]),
  role("operator", "Operator", ["apps.read", "apps.operate", "apps.deploy"], true, ["team", "project", "environment"]),
  role("admin", "Admin", NARROW, true, ["team", "project", "environment"]),
  role("owner", "Owner", TEAM, true, ["team"]),
  role("release-manager", "Release manager", ["apps.read", "apps.deploy"], false, ["environment", "project", "team", "instance"], "Ships what staging approved"),
];

const BINDING = { id: 3, username: "maria", role: "release-manager", scope: "acme/web", scope_kind: "project", team: "acme", project: "web", environment: null, created_at: 1_790_100_000, created_by: "ana" };
const MEMBERSHIP = { id: 0, username: "omar", role: "admin", scope: "acme", scope_kind: "team", team: "acme", project: null, environment: null, created_at: 1_790_000_000, created_by: "ana" };

function account(username: string, role_: string) {
  return { id: 1, username, display_name: username, role: role_, status: "active", mfa_enabled: true, passkeys: 0, backup_codes_remaining: 8, created_at: 1_790_000_000, failures_since_login: 0 };
}

interface Fixture {
  roles: unknown[];
  bindings: unknown[];
}

function accessBackend(initial: Partial<Fixture> = {}, extra: Record<string, RouteHandler> = {}) {
  const state: Fixture = { roles: ROLES, bindings: [BINDING, MEMBERSHIP], ...initial };
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...organisation(),
    "GET /api/access/roles": () => json(200, { roles: state.roles, permissions: PERMISSIONS, team_permissions: TEAM, narrow_permissions: NARROW }),
    "GET /api/access/bindings": () => json(200, { bindings: state.bindings, total: state.bindings.length }),
    "GET /api/auth/accounts": () => json(200, { accounts: [account("maria", "operator"), account("omar", "operator"), account("paul", "viewer")], conflicts: [] }),
    ...extra,
  });
  return { backend, state };
}

async function pageAt(initial: Partial<Fixture> = {}, extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const world = accessBackend(initial, extra);
  const harness = renderConsole("/settings/access");
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  return { ...harness, ...world };
}

describe("where a role can be held", () => {
  it("is anywhere for permissions a project can carry, a team or the server for governing ones, the server otherwise", () => {
    expect(roleScopes(["self", "apps.read"], NARROW, TEAM)).toEqual(["environment", "project", "team", "instance"]);
    expect(roleScopes(["apps.read", "teams.manage"], NARROW, TEAM)).toEqual(["team", "instance"]);
    expect(roleScopes(["apps.read", "accounts.manage"], NARROW, TEAM)).toEqual(["instance"]);
    expect(roleScopes([], NARROW, TEAM)).toEqual([]);
  });

  it("groups the permissions to choose by area and leaves out the one everybody has", () => {
    expect(groupPermissions(["self", "apps.read", "apps.deploy", "server.manage", "root_equivalent"])).toEqual([
      { area: "apps", permissions: ["apps.deploy", "apps.read"] },
      { area: "root_equivalent", permissions: ["root_equivalent"] },
      { area: "server", permissions: ["server.manage"] },
    ]);
  });
});

describe("Settings > Roles and access", () => {
  it("lists who holds what and where, and the roles, and passes axe", { timeout: 30_000 }, async () => {
    await pageAt();
    await screen.findByText("maria");
    const grants = screen.getByRole("region", { name: "Roles granted to accounts" });
    const mariaRow = within(grants).getByText("maria").closest("tr");
    if (!mariaRow) throw new Error("no row for maria");
    expect(within(mariaRow).getByText("Release manager")).toBeInTheDocument();
    expect(within(mariaRow).getByText("Custom")).toBeInTheDocument();
    expect(within(mariaRow).getByText("Project")).toBeInTheDocument();
    expect(within(mariaRow).getByText("acme/web")).toBeInTheDocument();
    // A team membership is the team's to manage.
    expect(within(grants).getByRole("link", { name: "Managed in the team" })).toHaveAttribute("href", "/teams/acme/members");
    const roles = await screen.findByRole("region", { name: "Role definitions" });
    expect(within(roles).getByText("Release manager")).toBeInTheDocument();
    expect(within(roles).getAllByText("Built in")).toHaveLength(4);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is in the settings navigation, as the central's section", async () => {
    await pageAt();
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "Roles" })).toHaveAttribute("aria-current", "page");
  });

  it("gives a role on an environment, offering only the roles that can be held there", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt(
      {},
      {
        "POST /api/access/bindings": (call: RecordedCall) => {
          const body = call.body as { username: string; role: string };
          return json(201, { ...BINDING, id: 9, username: body.username, role: body.role, scope: "acme/web/staging", scope_kind: "environment" });
        },
      },
    );
    await user.click(await screen.findByRole("button", { name: "Grant a role" }));
    const dialog = await screen.findByRole("dialog", { name: "Grant a role" });
    await user.click(within(dialog).getByRole("combobox", { name: "Account" }));
    await user.click(await screen.findByRole("option", { name: "paul" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Where it applies" }));
    await user.click(await screen.findByRole("option", { name: "An environment" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Team" }));
    await user.click(await screen.findByRole("option", { name: "Acme Corp" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Project" }));
    await user.click(await screen.findByRole("option", { name: "Web" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Environment" }));
    await user.click(await screen.findByRole("option", { name: "Staging" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Role" }));
    // Owner belongs to a whole team: it is not offered on an environment.
    expect(screen.queryByRole("option", { name: "Owner" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("option", { name: "Release manager" }));
    await user.click(within(dialog).getByRole("button", { name: "Grant role" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/access/bindings")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/access/bindings")[0]?.body).toEqual({
      username: "paul",
      role: "release-manager",
      scope: "environment",
      team: "acme",
      project: "web",
      environment: "staging",
    });
  });

  it("shows the server's refusal of a grant as it wrote it", { timeout: 30_000 }, async () => {
    const { user } = await pageAt(
      {},
      {
        "POST /api/access/bindings": () =>
          problem(400, "accesserror", "'paul' would govern and run the server at once", { hint: "Use another account for what it should be able to do." }),
      },
    );
    await user.click(await screen.findByRole("button", { name: "Grant a role" }));
    const dialog = await screen.findByRole("dialog", { name: "Grant a role" });
    await user.click(within(dialog).getByRole("combobox", { name: "Account" }));
    await user.click(await screen.findByRole("option", { name: "paul" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Where it applies" }));
    await user.click(await screen.findByRole("option", { name: "The whole server" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Role" }));
    await user.click(await screen.findByRole("option", { name: "Release manager" }));
    await user.click(within(dialog).getByRole("button", { name: "Grant role" }));
    expect(await within(dialog).findByText("'paul' would govern and run the server at once")).toBeInTheDocument();
    expect(within(dialog).getByText("Use another account for what it should be able to do.")).toBeInTheDocument();
  });

  it("revokes a grant from the place it was given, after asking", { timeout: 30_000 }, async () => {
    const { backend, state, user } = await pageAt({}, { "DELETE /api/access/bindings/3": () => json(200, { success: true, message: "Revoked" }) });
    await user.click(await screen.findByRole("button", { name: "Revoke Release manager from maria" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Revoke Release manager from maria" });
    expect(within(dialog).getByText(/loses what Release manager gave on acme\/web/)).toBeInTheDocument();
    state.bindings = [MEMBERSHIP];
    await user.click(within(dialog).getByRole("button", { name: "Revoke role" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/access/bindings/3")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByText("acme/web")).not.toBeInTheDocument();
    });
  });

  it("defines a role from the permissions that exist, and says where it can be held", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt(
      {},
      {
        "POST /api/access/roles": (call: RecordedCall) => {
          const body = call.body as { name: string; label: string | null; permissions: string[] };
          return json(201, role(body.name, body.label ?? body.name, body.permissions, false, ["environment", "project", "team", "instance"]));
        },
      },
    );
    await user.click(await screen.findByRole("button", { name: "Define a role" }));
    const drawer = await screen.findByRole("dialog", { name: "Define a role" });
    await user.type(within(drawer).getByLabelText(/^Name/), "db-reader");
    await user.type(within(drawer).getByLabelText(/^Label/), "Database reader");
    // The server's own sentence on each permission is the checkbox's description.
    await user.click(within(drawer).getByRole("checkbox", { name: /databases\.read/ }));
    expect(within(drawer).getByText("See databases")).toBeInTheDocument();
    expect(within(drawer).getByText("It can be held on: Environment, Project, Team, Server")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("checkbox", { name: /accounts\.manage/ }));
    expect(within(drawer).getByText("It can be held on: Server")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("checkbox", { name: /accounts\.manage/ }));
    await user.click(within(drawer).getByRole("button", { name: "Define role" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/access/roles")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/access/roles")[0]?.body).toEqual({
      name: "db-reader",
      label: "Database reader",
      description: null,
      permissions: ["databases.read"],
    });
  });

  it("shows what a built-in role holds without letting it be changed", { timeout: 30_000 }, async () => {
    const { user } = await pageAt();
    await user.click(await screen.findByRole("button", { name: "View Operator" }));
    const drawer = await screen.findByRole("dialog", { name: "Operator" });
    const boxes = within(drawer).getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    for (const box of boxes) {
      expect(box).toBeChecked();
      expect(box).toHaveAttribute("aria-disabled", "true");
    }
    expect(within(drawer).queryByRole("button", { name: "Save role" })).not.toBeInTheDocument();
  });

  it("deletes a role of your own after asking, and the server's refusal for one that is held is shown", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, { "DELETE /api/access/roles/release-manager": () => problem(409, "accesserror", "release-manager is held by maria") });
    await user.click(await screen.findByRole("button", { name: "Delete Release manager" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Release manager" });
    await user.click(within(dialog).getByRole("button", { name: "Delete role" }));
    expect(await within(dialog).findByText("release-manager is held by maria")).toBeInTheDocument();
    expect(backend.callsTo("DELETE /api/access/roles/release-manager")).toHaveLength(1);
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    await pageAt();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("region", { name: "Roles concedidos a cuentas" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Definir un rol" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
