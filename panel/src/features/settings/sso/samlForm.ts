/**
 * A SAML provider's form as text and switches, and back to what the API takes. The rules (which
 * certificate is acceptable, which sign-in address) are the server's and come back as the field's
 * own message; this only turns what is typed into the shape the request needs.
 */

import type { SamlProvider, SamlSettings } from "../../../api/queries/saml";
import { NEW_ADMISSION, formatMappings, parseMappings, splitList } from "./providerForm";
import type { AdmissionDraft } from "./providerForm";

export interface SamlDraft extends AdmissionDraft {
  name: string;
  label: string;
  enabled: boolean;
  /** Pasted metadata XML: read by the server, which fills what is not typed beside it. */
  metadata: string;
  entityId: string;
  ssoUrl: string;
  /** PEM blocks, one after the other. */
  certificates: string;
  spEntityId: string;
  subjectAttribute: string;
  emailAttribute: string;
  nameAttribute: string;
  usernameAttribute: string;
  groupsAttribute: string;
  clockSkew: string;
}

/** A provider nobody configured yet, with the defaults of the server. */
export const NEW_SAML_DRAFT: SamlDraft = {
  ...NEW_ADMISSION,
  // The organisation's own provider: its address is taken as verified, and a person without one
  // can still sign in.
  requireEmailVerified: false,
  assumeEmailVerified: true,
  name: "",
  label: "",
  enabled: true,
  metadata: "",
  entityId: "",
  ssoUrl: "",
  certificates: "",
  spEntityId: "",
  subjectAttribute: "",
  emailAttribute: "email",
  nameAttribute: "displayName",
  usernameAttribute: "",
  groupsAttribute: "groups",
  clockSkew: "60",
};

/** A provider the server has, as a form. */
export function samlDraftOf(info: SamlProvider): SamlDraft {
  return {
    ...NEW_ADMISSION,
    name: info.name,
    label: info.label,
    enabled: info.enabled,
    metadata: "",
    entityId: info.idp_entity_id,
    ssoUrl: info.sso_url,
    certificates: info.certificates.join("\n"),
    spEntityId: info.sp_entity_id,
    subjectAttribute: info.subject_attribute,
    emailAttribute: info.email_attribute,
    nameAttribute: info.name_attribute,
    usernameAttribute: info.username_attribute,
    groupsAttribute: info.groups_attribute,
    clockSkew: String(info.clock_skew_seconds),
    provisioning: info.provisioning === "invite" || info.provisioning === "auto" ? info.provisioning : "off",
    defaultRole: info.default_role,
    defaultTeam: info.default_team ?? "",
    defaultTeamRole: info.default_team_role ?? "",
    linkByEmail: info.link_by_email,
    requireEmailVerified: info.require_email_verified,
    assumeEmailVerified: info.assume_email_verified,
    allowedDomains: info.allowed_domains.join("\n"),
    requireAmr: info.require_amr.join(", "),
    groupSync: info.group_sync,
    mappings: formatMappings(info.group_mappings),
  };
}

/** The certificates typed, one entry per PEM block; text that holds none goes as one entry for the server to judge. */
export function splitCertificates(text: string): string[] {
  const blocks = text.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g);
  if (blocks !== null) return blocks;
  const trimmed = text.trim();
  return trimmed === "" ? [] : [trimmed];
}

/**
 * The request for a draft, or the number of the mappings line that is not a mapping. Every setting
 * is sent: a form shows them all, so what is on screen is what is saved. The metadata is sent only
 * when pasted, and the identity provider's three settings only when they are not empty, so that a
 * new provider can be made from its metadata alone.
 */
export function samlSettingsOf(
  draft: SamlDraft,
): { settings: SamlSettings; badLine: null } | { settings: null; badLine: number } {
  const parsed = parseMappings(draft.mappings);
  if (parsed.error !== null) return { settings: null, badLine: parsed.error };
  const skew = Number.parseInt(draft.clockSkew, 10);
  const certificates = splitCertificates(draft.certificates);
  return {
    settings: {
      label: draft.label.trim(),
      enabled: draft.enabled,
      sp_entity_id: draft.spEntityId.trim(),
      subject_attribute: draft.subjectAttribute.trim(),
      email_attribute: draft.emailAttribute.trim(),
      name_attribute: draft.nameAttribute.trim(),
      username_attribute: draft.usernameAttribute.trim(),
      groups_attribute: draft.groupsAttribute.trim(),
      provisioning: draft.provisioning,
      default_role: draft.defaultRole,
      // Empty clears: the API leaves a null alone.
      default_team: draft.defaultTeam,
      default_team_role: draft.defaultTeam === "" ? "" : draft.defaultTeamRole,
      link_by_email: draft.linkByEmail,
      require_email_verified: draft.requireEmailVerified,
      assume_email_verified: draft.assumeEmailVerified,
      allowed_domains: splitList(draft.allowedDomains),
      require_amr: splitList(draft.requireAmr),
      group_sync: draft.groupSync,
      group_mappings: parsed.mappings,
      ...(draft.metadata.trim() !== "" ? { idp_metadata: draft.metadata } : {}),
      ...(draft.entityId.trim() !== "" ? { idp_entity_id: draft.entityId.trim() } : {}),
      ...(draft.ssoUrl.trim() !== "" ? { sso_url: draft.ssoUrl.trim() } : {}),
      ...(certificates.length > 0 ? { certificates } : {}),
      ...(Number.isNaN(skew) ? {} : { clock_skew_seconds: skew }),
    },
    badLine: null,
  };
}
