/**
 * A directory's form as text and switches, and back to what the API takes. The rules (which
 * filter is a filter, which name is a host) are the server's and come back as the field's own
 * message; this only turns what is typed into the shape the request needs.
 */

import type { Directory, DirectorySettings } from "../../../api/queries/ldap";
import { NEW_ADMISSION, formatMappings, parseMappings, splitList } from "./providerForm";
import type { AdmissionDraft } from "./providerForm";

export const TLS_MODES = ["ldaps", "starttls", "none"] as const;
export type TlsMode = (typeof TLS_MODES)[number];

export interface DirectoryDraft extends AdmissionDraft {
  name: string;
  label: string;
  enabled: boolean;
  host: string;
  port: string;
  tls: TlsMode;
  caFile: string;
  bindDn: string;
  /** What was typed: empty keeps the stored one. */
  bindPassword: string;
  clearBindPassword: boolean;
  userBase: string;
  userFilter: string;
  subjectAttribute: string;
  emailAttribute: string;
  nameAttribute: string;
  usernameAttribute: string;
  groupsAttribute: string;
  groupBase: string;
  groupFilter: string;
  timeout: string;
}

/** A directory nobody configured yet, with the defaults of an OpenLDAP. */
export const NEW_DIRECTORY_DRAFT: DirectoryDraft = {
  ...NEW_ADMISSION,
  // A directory is the organisation's own: its address is taken as verified, and a person
  // without one can still sign in.
  requireEmailVerified: false,
  assumeEmailVerified: true,
  name: "",
  label: "",
  enabled: true,
  host: "",
  port: "636",
  tls: "ldaps",
  caFile: "",
  bindDn: "",
  bindPassword: "",
  clearBindPassword: false,
  userBase: "",
  userFilter: "(&(objectClass=person)(uid={username}))",
  subjectAttribute: "entryUUID",
  emailAttribute: "mail",
  nameAttribute: "displayName",
  usernameAttribute: "uid",
  groupsAttribute: "memberOf",
  groupBase: "",
  groupFilter: "",
  timeout: "10",
};

/** The mode the server stored, or the safe one for anything this console does not know. */
function tlsOf(value: string): TlsMode {
  return TLS_MODES.find((mode) => mode === value) ?? "ldaps";
}

/** A directory the server has, as a form. */
export function directoryDraftOf(info: Directory): DirectoryDraft {
  return {
    ...NEW_ADMISSION,
    name: info.name,
    label: info.label,
    enabled: info.enabled,
    host: info.host,
    port: String(info.port),
    tls: tlsOf(info.tls),
    caFile: info.ca_file ?? "",
    bindDn: info.bind_dn,
    bindPassword: "",
    clearBindPassword: false,
    userBase: info.user_base,
    userFilter: info.user_filter,
    subjectAttribute: info.subject_attribute,
    emailAttribute: info.email_attribute,
    nameAttribute: info.name_attribute,
    usernameAttribute: info.username_attribute,
    groupsAttribute: info.groups_attribute,
    groupBase: info.group_base,
    groupFilter: info.group_filter,
    timeout: String(info.timeout_seconds),
    provisioning: info.provisioning === "invite" || info.provisioning === "auto" ? info.provisioning : "off",
    defaultRole: info.default_role,
    defaultTeam: info.default_team ?? "",
    defaultTeamRole: info.default_team_role ?? "",
    linkByEmail: info.link_by_email,
    requireEmailVerified: info.require_email_verified,
    assumeEmailVerified: info.assume_email_verified,
    allowedDomains: info.allowed_domains.join("\n"),
    groupSync: info.group_sync,
    mappings: formatMappings(info.group_mappings),
  };
}

/**
 * The request for a draft, or the number of the mappings line that is not a mapping. Every
 * setting is sent: a form shows them all, so what is on screen is what is saved. The password
 * is sent only when one was typed, and a stored one is removed only when asked.
 */
export function directorySettingsOf(
  draft: DirectoryDraft,
): { settings: DirectorySettings; badLine: null } | { settings: null; badLine: number } {
  const parsed = parseMappings(draft.mappings);
  if (parsed.error !== null) return { settings: null, badLine: parsed.error };
  const port = Number.parseInt(draft.port, 10);
  const timeout = Number.parseInt(draft.timeout, 10);
  return {
    settings: {
      label: draft.label.trim(),
      enabled: draft.enabled,
      host: draft.host.trim(),
      tls: draft.tls,
      ca_file: draft.caFile.trim(),
      bind_dn: draft.bindDn.trim(),
      user_base: draft.userBase.trim(),
      user_filter: draft.userFilter.trim(),
      subject_attribute: draft.subjectAttribute.trim(),
      email_attribute: draft.emailAttribute.trim(),
      name_attribute: draft.nameAttribute.trim(),
      username_attribute: draft.usernameAttribute.trim(),
      groups_attribute: draft.groupsAttribute.trim(),
      group_base: draft.groupBase.trim(),
      group_filter: draft.groupFilter.trim(),
      provisioning: draft.provisioning,
      default_role: draft.defaultRole,
      // Empty clears: the API leaves a null alone.
      default_team: draft.defaultTeam,
      default_team_role: draft.defaultTeam === "" ? "" : draft.defaultTeamRole,
      link_by_email: draft.linkByEmail,
      require_email_verified: draft.requireEmailVerified,
      assume_email_verified: draft.assumeEmailVerified,
      allowed_domains: splitList(draft.allowedDomains),
      group_sync: draft.groupSync,
      group_mappings: parsed.mappings,
      clear_bind_password: draft.clearBindPassword,
      ...(Number.isNaN(port) ? {} : { port }),
      ...(Number.isNaN(timeout) ? {} : { timeout_seconds: timeout }),
      ...(draft.bindPassword !== "" ? { bind_password: draft.bindPassword } : {}),
    },
    badLine: null,
  };
}
