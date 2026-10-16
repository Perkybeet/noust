import { describe, expect, it } from "vitest";

import { formatMappings, parseMappings } from "./providerForm";

describe("the group mappings box", () => {
  it("reads the three shapes of a line and writes them back the same", () => {
    const text = "ops=admin\ndevs=acme:operator\nrelease=release-manager@acme/web\nqa=viewer@acme/web/staging";
    const parsed = parseMappings(text);
    expect(parsed.error).toBeNull();
    expect(parsed.mappings).toEqual([
      { group: "ops", role: "admin" },
      { group: "devs", team: "acme", team_role: "operator" },
      { group: "release", bind: "release-manager", team: "acme", project: "web" },
      { group: "qa", bind: "viewer", team: "acme", project: "web", environment: "staging" },
    ]);
    expect(formatMappings(parsed.mappings ?? [])).toBe(text);
  });

  it("takes everything before the last equals sign as the group, so a directory's group is one", () => {
    const parsed = parseMappings("cn=ops,ou=groups,dc=acme,dc=test=admin");
    expect(parsed.mappings).toEqual([{ group: "cn=ops,ou=groups,dc=acme,dc=test", role: "admin" }]);
  });

  it("points at the line that names a scope badly", () => {
    expect(parseMappings("ok=admin\nrelease=release-manager@").error).toBe(2);
    expect(parseMappings("release=release-manager@acme//staging").error).toBe(1);
    expect(parseMappings("release=@acme/web").error).toBe(1);
    expect(parseMappings("release=release-manager@a/b/c/d").error).toBe(1);
    expect(parseMappings("nonsense").error).toBe(1);
  });
});
