import { describe, expect, it } from "vitest";

import type { AccessInput } from "./access";
import { holds } from "./access";

const ROLES = {
  viewer: ["self", "apps.read", "teams.read"],
  admin: ["self", "apps.read", "apps.manage", "teams.read", "teams.manage"],
};
const TEAM_ROLES = {
  viewer: ["self", "apps.read", "teams.read"],
  admin: ["self", "apps.read", "apps.manage", "teams.read"],
  owner: ["self", "apps.read", "apps.manage", "teams.read", "teams.manage"],
};

/** A viewer on the server who owns acme and only reads globex. */
const OWNER_OF_ACME: AccessInput = {
  session: { role: "viewer", account: { username: "carla", role: "viewer" } as never, permissions: ["self", "apps.read", "apps.manage", "teams.read", "teams.manage"] },
  rolePermissions: ROLES,
  teamRoles: { acme: "owner", globex: "viewer" },
  teamRolePermissions: TEAM_ROLES,
};

describe("what a principal may do in a team", () => {
  it("lets an owner govern its own team and not another it only reads", () => {
    expect(holds(OWNER_OF_ACME, "teams.manage", "acme")).toBe(true);
    expect(holds(OWNER_OF_ACME, "teams.manage", "globex")).toBe(false);
    expect(holds(OWNER_OF_ACME, "apps.manage", "acme")).toBe(true);
    expect(holds(OWNER_OF_ACME, "apps.manage", "globex")).toBe(false);
  });

  it("answers with the ceiling when no team is named", () => {
    expect(holds(OWNER_OF_ACME, "teams.manage")).toBe(true);
    expect(holds(OWNER_OF_ACME, "server.manage")).toBe(false);
  });

  it("never gives a viewer a permission that is nobody's in its teams", () => {
    const viewer: AccessInput = {
      ...OWNER_OF_ACME,
      session: { role: "viewer", account: { username: "vic", role: "viewer" } as never, permissions: ["self", "apps.read", "teams.read"] },
      teamRoles: { acme: "viewer" },
    };
    expect(holds(viewer, "teams.manage", "acme")).toBe(false);
    expect(holds(viewer, "apps.manage")).toBe(false);
    expect(holds(viewer, "apps.read", "acme")).toBe(true);
  });

  it("keeps an instance administrator's own permissions in every team", () => {
    const admin: AccessInput = {
      session: { role: "admin", account: { username: "ana", role: "admin" } as never, permissions: ROLES.admin },
      rolePermissions: ROLES,
      teamRoles: {},
      teamRolePermissions: TEAM_ROLES,
    };
    expect(holds(admin, "teams.manage", "acme")).toBe(true);
  });

  it("is the ceiling for a credential with no account", () => {
    const token: AccessInput = {
      session: { role: null, account: null, permissions: ["self", "teams.manage"] },
      rolePermissions: ROLES,
      teamRoles: {},
      teamRolePermissions: TEAM_ROLES,
    };
    expect(holds(token, "teams.manage", "acme")).toBe(true);
    expect(holds(token, "apps.manage", "acme")).toBe(false);
  });

  it("does not hide what is allowed while the role tables are still being read", () => {
    expect(holds({ ...OWNER_OF_ACME, rolePermissions: undefined }, "teams.manage", "globex")).toBe(true);
    expect(holds({ ...OWNER_OF_ACME, teamRolePermissions: undefined }, "teams.manage", "acme")).toBe(true);
  });

  it("holds nothing before the session is known", () => {
    expect(holds({ ...OWNER_OF_ACME, session: undefined }, "apps.read")).toBe(false);
  });
});
