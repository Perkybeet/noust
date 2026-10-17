import { describe, expect, it } from "vitest";

import { NEW_DRAFT, draftOf, formatMappings, parseMappings, settingsOf } from "./providerForm";
import type { SsoProviderInfo } from "../../../api/queries/ssoAdmin";

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

describe("a GitHub provider's form", () => {
  const github = { ...NEW_DRAFT, kind: "github" as const, name: "github", clientId: "Iv1.abc123" };

  it("sends no issuer and no scopes while github.com is the host", () => {
    const { settings } = settingsOf(github);
    expect(settings).toMatchObject({ kind: "github", client_id: "Iv1.abc123" });
    expect(settings).not.toHaveProperty("issuer");
    expect(settings).not.toHaveProperty("scopes");
  });

  it("sends the Enterprise Server when one is named", () => {
    const { settings } = settingsOf({ ...github, issuer: " https://ghe.example.com " });
    expect(settings).toMatchObject({ kind: "github", issuer: "https://ghe.example.com" });
  });

  it("still sends an OpenID Connect provider's issuer and scopes", () => {
    const { settings } = settingsOf({ ...NEW_DRAFT, issuer: "https://sso.example.com", clientId: "noust" });
    expect(settings).toMatchObject({ kind: "oidc", issuer: "https://sso.example.com", scopes: "openid email profile" });
  });

  it("reads the kind back from a provider, and a server that does not say is OpenID Connect", () => {
    const info = { name: "corp", label: "", issuer: "https://x", client_id: "c", scopes: "", group_mappings: [], allowed_domains: [], require_amr: [] } as unknown as SsoProviderInfo;
    expect(draftOf({ ...info, kind: "github" }).kind).toBe("github");
    expect(draftOf(info).kind).toBe("oidc");
  });
});
