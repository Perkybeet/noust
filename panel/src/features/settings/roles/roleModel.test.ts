import { describe, expect, it } from "vitest";

import type { Role } from "../../../api/queries/roles";
import { translate } from "../../../i18n";
import { effectivePermissions, groupPermissions, kindOf, roleDescription, roleLabel } from "./roleModel";

function role(over: Partial<Role> & { name: string }): Role {
  return {
    kind: "seeded",
    label: over.name,
    description: "",
    permissions: [],
    shipped_permissions: [],
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
    created_at: 0,
    updated_at: 0,
    ...over,
  };
}

const en = (key: string): string => (translate as unknown as (locale: string, key: string) => string)("en", key);
const es = (key: string): string => (translate as unknown as (locale: string, key: string) => string)("es", key);
const tEn = Object.assign(en, { locale: "en" }) as never;
const tEs = Object.assign(es, { locale: "es" }) as never;

describe("what kind a role is", () => {
  it("is the owner when it is protected, whatever else it says", () => {
    expect(kindOf(role({ name: "owner", protected: true, edited: true }))).toBe("owner");
  });
  it("follows Noust until somebody edits it, and is yours when you made it", () => {
    expect(kindOf(role({ name: "viewer" }))).toBe("follows");
    expect(kindOf(role({ name: "viewer", edited: true }))).toBe("edited");
    expect(kindOf(role({ name: "release-manager", kind: "custom" }))).toBe("custom");
  });
});

describe("the words of a role", () => {
  it("says a shipped role in the console's language, until somebody renames it", () => {
    expect(roleLabel(tEn, role({ name: "operator", label: "Operator" }))).toBe("Operator");
    // Somebody renamed it: their name stays, in every language.
    expect(roleLabel(tEs, role({ name: "operator", label: "Deployer", edited: true }))).toBe("Deployer");
  });
  it("keeps the name of a role of your own and its description", () => {
    expect(roleLabel(tEs, role({ name: "release-manager", kind: "custom", label: "Release manager" }))).toBe("Release manager");
    expect(roleDescription(tEs, role({ name: "release-manager", kind: "custom", description: "Ships on Fridays" }))).toBe("Ships on Fridays");
  });
  it("shows the description the operator wrote on an edited role", () => {
    expect(roleDescription(tEs, role({ name: "viewer", edited: true, description: "Only the dashboards" }))).toBe("Only the dashboards");
    expect(roleDescription(tEn, role({ name: "viewer" }))).toContain("secrets");
  });
});

describe("what an account can do", () => {
  const roles = [
    role({ name: "viewer", permissions: ["apps.read", "self", "backups.read"] }),
    role({ name: "operator", permissions: ["apps.read", "apps.deploy", "self"] }),
  ];
  it("is every permission the roles give, and which role gives it", () => {
    expect(effectivePermissions(["operator", "viewer"], roles)).toEqual([
      { permission: "apps.deploy", from: ["operator"] },
      { permission: "apps.read", from: ["operator", "viewer"] },
      { permission: "backups.read", from: ["viewer"] },
    ]);
  });
  it("leaves out what everybody has and a role that does not exist", () => {
    expect(effectivePermissions(["ghost"], roles)).toEqual([]);
    expect(effectivePermissions(["viewer"], roles).map((item) => item.permission)).not.toContain("self");
  });
});

describe("grouping permissions for choosing", () => {
  it("groups by area, sorted, without the permission everybody has", () => {
    expect(groupPermissions(["server.read", "apps.read", "self", "apps.deploy"])).toEqual([
      { area: "apps", permissions: ["apps.deploy", "apps.read"] },
      { area: "server", permissions: ["server.read"] },
    ]);
  });
});
