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

function roleOut(name: string, label: string, permissions: string[], over: Record<string, unknown> = {}) {
  return {
    name,
    kind: "seeded",
    label,
    description: `${label} description`,
    permissions: ["self", ...permissions],
    shipped_permissions: ["self", ...permissions],
    edited: false,
    follows_noust: true,
    protected: false,
    offers: [],
    holders: [],
    holder_count: 0,
    bindings: 0,
    joins_duties: false,
    governing: [],
    running: [],
    can_edit: true,
    can_delete: false,
    can_reset: false,
    can_duplicate: true,
    created_at: 1_790_000_000,
    updated_at: 1_790_000_000,
    ...over,
  };
}

const SERVER_ROLES = [
  roleOut("viewer", "Viewer", ["apps.read"], { holders: ["paul"], holder_count: 1 }),
  roleOut("operator", "Operator", ["apps.read", "apps.deploy"], { edited: true, offers: ["apps.operate"], can_reset: true, holders: ["maria", "omar"], holder_count: 2 }),
  roleOut("owner", "Owner", ["apps.read", "apps.deploy", "accounts.manage"], { kind: "protected", protected: true, can_edit: false, can_duplicate: false }),
  roleOut("release-manager", "Release manager", ["apps.read", "apps.deploy"], { kind: "custom", can_delete: true, description: "Ships what staging approved" }),
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
    "GET /api/roles": () => json(200, { roles: SERVER_ROLES, permissions: PERMISSIONS, owners: 1, duties_apart: false }),
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
  await screen.findByRole("heading", { level: 1, name: "Roles" });
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
    const roles = await screen.findByRole("region", { name: "Roles" });
    expect(within(roles).getByText("Release manager")).toBeInTheDocument();
    // Noust's own, the edited one and yours are told apart, and the owner says it cannot be changed.
    expect(within(roles).getByText("Yours")).toBeInTheDocument();
    expect(within(roles).getByText("Edited")).toBeInTheDocument();
    expect(within(roles).getAllByText("Owner")).toHaveLength(2);
    expect(within(roles).getByText("2 people")).toBeInTheDocument();
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

  it("defines a role from the permissions that exist, with the server's sentence on each", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt(
      {},
      {
        "POST /api/roles": (call: RecordedCall) => {
          const body = call.body as { name: string; label: string | null; permissions: string[] };
          return json(201, roleOut(body.name, body.label ?? body.name, body.permissions, { kind: "custom" }));
        },
      },
    );
    await user.click(await screen.findByRole("button", { name: "New role" }));
    const drawer = await screen.findByRole("dialog", { name: "New role" });
    await user.type(within(drawer).getByLabelText(/^Name/), "db-reader");
    await user.type(within(drawer).getByLabelText(/^Title/), "Database reader");
    await user.click(within(drawer).getByRole("checkbox", { name: /databases\.read/ }));
    expect(within(drawer).getByText("See databases")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Create role" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/roles")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/roles")[0]?.body).toEqual({ name: "db-reader", label: "Database reader", description: null, permissions: ["databases.read"] });
  });

  it("edits a role Noust ships like any other, and says that doing so makes it yours", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, { "PATCH /api/roles/viewer": () => json(200, roleOut("viewer", "Viewer", ["apps.read", "backups.read"], { edited: true })) });
    await user.click(await screen.findByRole("button", { name: "Actions for Viewer" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit Viewer" });
    expect(within(drawer).getByText(/stops following new versions/)).toBeInTheDocument();
    await user.click(within(drawer).getByRole("checkbox", { name: /backups\.read/ }));
    await user.click(within(drawer).getByRole("button", { name: "Save role" }));
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/roles/viewer")).toHaveLength(1);
    });
    expect((backend.callsTo("PATCH /api/roles/viewer")[0]?.body as { permissions: string[] }).permissions).toEqual(["apps.read", "backups.read"]);
  });

  it("shows what the owner holds and never offers to change it", { timeout: 30_000 }, async () => {
    const { user } = await pageAt();
    await user.click(await screen.findByRole("button", { name: "Actions for Owner" }));
    await user.click(await screen.findByRole("menuitem", { name: "View" }));
    const drawer = await screen.findByRole("dialog", { name: "Owner" });
    for (const box of within(drawer).getAllByRole("checkbox")) {
      expect(box).toBeChecked();
      expect(box).toHaveAttribute("aria-disabled", "true");
    }
    expect(within(drawer).queryByRole("button", { name: "Save role" })).not.toBeInTheDocument();
  });

  it("offers the permission a newer version adds to an edited role, and takes it when asked", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, { "POST /api/roles/operator/offers": () => json(200, roleOut("operator", "Operator", ["apps.read", "apps.deploy", "apps.operate"], { edited: true })) });
    expect(await screen.findByText("1 new permission")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Actions for Operator" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit Operator" });
    expect(within(drawer).getAllByText("apps.operate").length).toBeGreaterThan(0);
    await user.click(within(drawer).getByRole("button", { name: "Add them" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/roles/operator/offers")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/roles/operator/offers")[0]?.body).toEqual({ accept: ["apps.operate"] });
  });

  it("puts a role back as it comes, after asking, and deletes one of your own only when the server admits it", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, { "POST /api/roles/operator/reset": () => json(200, roleOut("operator", "Operator", ["apps.read", "apps.deploy"])), "DELETE /api/roles/release-manager": () => json(200, { success: true, message: "Deleted" }) });
    await user.click(await screen.findByRole("button", { name: "Actions for Operator" }));
    await user.click(await screen.findByRole("menuitem", { name: "Put back as it comes" }));
    await user.click(await within(await screen.findByRole("alertdialog", { name: "Put Operator back as it comes" })).findByRole("button", { name: "Put it back" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/roles/operator/reset")).toHaveLength(1);
    });
    // The shipped roles cannot be deleted, so their menu has no Delete; the one of your own has.
    await user.click(await screen.findByRole("button", { name: "Actions for Viewer" }));
    expect(screen.queryByRole("menuitem", { name: "Delete" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(await screen.findByRole("button", { name: "Actions for Release manager" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    await user.click(await within(await screen.findByRole("alertdialog", { name: "Delete Release manager" })).findByRole("button", { name: "Delete role" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/roles/release-manager")).toHaveLength(1);
    });
  });

  it("duplicates a role into one of your own", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, { "POST /api/roles/viewer/duplicate": () => json(201, roleOut("viewer-copy", "Viewer", ["apps.read"], { kind: "custom" })) });
    await user.click(await screen.findByRole("button", { name: "Actions for Viewer" }));
    await user.click(await screen.findByRole("menuitem", { name: "Duplicate" }));
    const dialog = await screen.findByRole("dialog", { name: "Duplicate Viewer" });
    expect(within(dialog).getByLabelText(/^Name of the copy/)).toHaveValue("viewer-copy");
    await user.click(within(dialog).getByRole("button", { name: "Duplicate role" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/roles/viewer/duplicate")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/roles/viewer/duplicate")[0]?.body).toEqual({ name: "viewer-copy" });
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    await pageAt();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("region", { name: "Roles concedidos a cuentas" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nuevo rol" })).toBeInTheDocument();
    // The roles Noust ships are said in the console's language until somebody renames them.
    expect(screen.getByText("Operador")).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
