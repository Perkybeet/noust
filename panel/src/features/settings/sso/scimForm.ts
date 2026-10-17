/**
 * A SCIM source's form as text and switches, and back to what the API takes. The rules (which
 * role a provider may create, which team exists) are the server's and come back as the field's
 * own message; this only turns what is typed into the shape the request needs.
 */

import type { ScimSource, ScimSourceSettings } from "../../../api/queries/scim";
import { NEW_ADMISSION, formatMappings, parseMappings, splitList } from "./providerForm";
import type { AdmissionDraft } from "./providerForm";

export interface ScimDraft extends AdmissionDraft {
  name: string;
  label: string;
  enabled: boolean;
  /** Issue a new token: the old one stops working at once. */
  rotateToken: boolean;
}

/** A source nobody configured yet, as the server's own defaults are. */
export const NEW_SCIM_DRAFT: ScimDraft = {
  ...NEW_ADMISSION,
  name: "",
  label: "",
  enabled: true,
  rotateToken: false,
};

/** A source the server has, as a form. */
export function scimDraftOf(info: ScimSource): ScimDraft {
  return {
    ...NEW_ADMISSION,
    name: info.name,
    label: info.label,
    enabled: info.enabled,
    rotateToken: false,
    defaultRole: info.default_role,
    defaultTeam: info.default_team ?? "",
    defaultTeamRole: info.default_team_role ?? "",
    linkByEmail: info.link_by_email,
    allowedDomains: info.allowed_domains.join("\n"),
    groupSync: info.group_sync,
    mappings: formatMappings(info.group_mappings),
  };
}

/**
 * The request for a draft, or the number of the mappings line that is not a mapping. Every
 * setting is sent: a form shows them all, so what is on screen is what is saved.
 */
export function scimSettingsOf(
  draft: ScimDraft,
): { settings: ScimSourceSettings; badLine: null } | { settings: null; badLine: number } {
  const parsed = parseMappings(draft.mappings);
  if (parsed.error !== null) return { settings: null, badLine: parsed.error };
  return {
    settings: {
      label: draft.label.trim(),
      enabled: draft.enabled,
      default_role: draft.defaultRole,
      default_team: draft.defaultTeam === "" ? null : draft.defaultTeam,
      default_team_role: draft.defaultTeam === "" || draft.defaultTeamRole === "" ? null : draft.defaultTeamRole,
      link_by_email: draft.linkByEmail,
      allowed_domains: splitList(draft.allowedDomains),
      group_sync: draft.groupSync,
      group_mappings: parsed.mappings,
      rotate_token: draft.rotateToken,
    },
    badLine: null,
  };
}
