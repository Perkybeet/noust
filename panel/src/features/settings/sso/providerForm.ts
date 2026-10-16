/**
 * A provider's form as text and switches, and back to what the API takes. The rules (which issuer
 * is acceptable, which role a created account may hold) are the server's and come back as the
 * field's own message; this only turns what is typed into the shape the request needs.
 */

import type { SsoGroupMapping, SsoProviderInfo, SsoProviderSettings } from "../../../api/queries/ssoAdmin";

/** What a first sign-in of a person nobody linked may do. */
export const PROVISIONING = ["off", "invite", "auto"] as const;
export type Provisioning = (typeof PROVISIONING)[number];

/** The roles an account created by a sign-in may hold; a person gives `security` and `auditor`. */
export const CREATED_ROLES = ["viewer", "operator", "admin"] as const;

export interface ProviderDraft {
  name: string;
  label: string;
  issuer: string;
  clientId: string;
  /** What was typed: empty keeps the stored one. */
  clientSecret: string;
  clearSecret: boolean;
  enabled: boolean;
  scopes: string;
  provisioning: Provisioning;
  defaultRole: string;
  defaultTeam: string;
  defaultTeamRole: string;
  linkByEmail: boolean;
  requireEmailVerified: boolean;
  assumeEmailVerified: boolean;
  /** One domain per line, or separated by commas. */
  allowedDomains: string;
  requireAmr: string;
  groupSync: boolean;
  groupsClaim: string;
  /** One `GROUP=ROLE` or `GROUP=TEAM:ROLE` per line. */
  mappings: string;
  clockSkew: string;
  redirectUri: string;
}

/** The defaults of a provider nobody configured yet, as the server's own are. */
export const NEW_DRAFT: ProviderDraft = {
  name: "",
  label: "",
  issuer: "",
  clientId: "",
  clientSecret: "",
  clearSecret: false,
  enabled: true,
  scopes: "openid email profile",
  provisioning: "off",
  defaultRole: "viewer",
  defaultTeam: "",
  defaultTeamRole: "",
  linkByEmail: false,
  requireEmailVerified: true,
  assumeEmailVerified: false,
  allowedDomains: "",
  requireAmr: "",
  groupSync: false,
  groupsClaim: "groups",
  mappings: "",
  clockSkew: "60",
  redirectUri: "",
};

/** Words separated by commas, spaces or new lines, with nothing empty. */
export function splitList(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/** The `TEAM/PROJECT/ENVIRONMENT` a role is held at: what is named, in order. */
function scopeOf(mapping: SsoGroupMapping): string {
  return [mapping.team, mapping.project, mapping.environment]
    .filter((part): part is string => part !== null && part !== undefined && part !== "")
    .join("/");
}

/**
 * `ops=admin`, `devs=acme:operator` and `release=release-manager@acme/web`, one per line: what each
 * group of the provider means. A role on the server, a membership of a team, a role held at a scope.
 */
export function formatMappings(mappings: readonly SsoGroupMapping[]): string {
  return mappings
    .map((mapping) => {
      if (mapping.bind !== null && mapping.bind !== undefined && mapping.bind !== "") {
        return `${mapping.group}=${mapping.bind}@${scopeOf(mapping)}`;
      }
      const meaning =
        mapping.team !== null && mapping.team !== undefined
          ? `${mapping.team}:${mapping.team_role ?? ""}`
          : (mapping.role ?? "");
      return `${mapping.group}=${meaning}`;
    })
    .join("\n");
}

export type ParsedMappings = { mappings: SsoGroupMapping[]; error: null } | { mappings: null; error: number };

/**
 * The lines of a mappings box as the API takes them. A line that is not `GROUP=ROLE`,
 * `GROUP=TEAM:ROLE` or `GROUP=ROLE@TEAM/PROJECT` is refused by its number, so the form can point
 * at it; whether the role or the team exists is the server's to say. The group is everything
 * before the last `=`: a directory's group is a name with `=` in it.
 */
export function parseMappings(text: string): ParsedMappings {
  const mappings: SsoGroupMapping[] = [];
  const lines = text.split("\n");
  for (const [index, raw] of lines.entries()) {
    const line = raw.trim();
    if (line === "") continue;
    const at = line.lastIndexOf("=");
    const group = at > 0 ? line.slice(0, at).trim() : "";
    const meaning = at > 0 ? line.slice(at + 1).trim() : "";
    if (group === "" || meaning === "") return { mappings: null, error: index + 1 };
    const sign = meaning.indexOf("@");
    if (sign >= 0) {
      const bind = meaning.slice(0, sign).trim();
      const [team = "", project, environment, ...rest] = meaning
        .slice(sign + 1)
        .split("/")
        .map((part) => part.trim());
      if (bind === "" || team === "" || rest.length > 0 || project === "" || environment === "") {
        return { mappings: null, error: index + 1 };
      }
      mappings.push({
        group,
        bind,
        team,
        ...(project === undefined ? {} : { project }),
        ...(environment === undefined ? {} : { environment }),
      });
      continue;
    }
    const colon = meaning.indexOf(":");
    if (colon < 0) {
      mappings.push({ group, role: meaning });
      continue;
    }
    const team = meaning.slice(0, colon).trim();
    const teamRole = meaning.slice(colon + 1).trim();
    if (team === "" || teamRole === "") return { mappings: null, error: index + 1 };
    mappings.push({ group, team, team_role: teamRole });
  }
  return { mappings, error: null };
}

/** A provider the server has, as a form. */
export function draftOf(info: SsoProviderInfo): ProviderDraft {
  return {
    name: info.name,
    label: info.label,
    issuer: info.issuer,
    clientId: info.client_id,
    clientSecret: "",
    clearSecret: false,
    enabled: info.enabled,
    scopes: info.scopes,
    provisioning: PROVISIONING.find((value) => value === info.provisioning) ?? "off",
    defaultRole: info.default_role,
    defaultTeam: info.default_team ?? "",
    defaultTeamRole: info.default_team_role ?? "",
    linkByEmail: info.link_by_email,
    requireEmailVerified: info.require_email_verified,
    assumeEmailVerified: info.assume_email_verified,
    allowedDomains: info.allowed_domains.join("\n"),
    requireAmr: info.require_amr.join(", "),
    groupSync: info.group_sync,
    groupsClaim: info.groups_claim,
    mappings: formatMappings(info.group_mappings),
    clockSkew: String(info.clock_skew_seconds),
    // Empty keeps what is stored; the address in force is shown beside the field.
    redirectUri: "",
  };
}

/**
 * The request for a draft, or the number of the mappings line that is not a mapping. Every
 * setting is sent: a form shows them all, so what is on screen is what is saved. The secret is
 * sent only when one was typed, and a stored one is removed only when asked.
 */
export function settingsOf(draft: ProviderDraft): { settings: SsoProviderSettings; badLine: null } | { settings: null; badLine: number } {
  const parsed = parseMappings(draft.mappings);
  if (parsed.error !== null) return { settings: null, badLine: parsed.error };
  const skew = Number.parseInt(draft.clockSkew, 10);
  const settings: SsoProviderSettings = {
    label: draft.label.trim(),
    issuer: draft.issuer.trim(),
    client_id: draft.clientId.trim(),
    enabled: draft.enabled,
    scopes: draft.scopes.trim(),
    provisioning: draft.provisioning,
    default_role: draft.defaultRole,
    default_team: draft.defaultTeam === "" ? null : draft.defaultTeam,
    default_team_role: draft.defaultTeam === "" || draft.defaultTeamRole === "" ? null : draft.defaultTeamRole,
    link_by_email: draft.linkByEmail,
    require_email_verified: draft.requireEmailVerified,
    assume_email_verified: draft.assumeEmailVerified,
    allowed_domains: splitList(draft.allowedDomains),
    require_amr: splitList(draft.requireAmr),
    group_sync: draft.groupSync,
    groups_claim: draft.groupsClaim.trim(),
    group_mappings: parsed.mappings,
    clear_secret: draft.clearSecret,
    ...(Number.isNaN(skew) ? {} : { clock_skew_seconds: skew }),
    ...(draft.redirectUri.trim() === "" ? {} : { redirect_uri: draft.redirectUri.trim() }),
    ...(draft.clientSecret !== "" ? { client_secret: draft.clientSecret } : {}),
  };
  return { settings, badLine: null };
}
