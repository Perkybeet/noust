import { describe, expect, it } from "vitest";

import type { SamlProvider } from "../../../api/queries/saml";
import { NEW_SAML_DRAFT, samlDraftOf, samlSettingsOf, splitCertificates } from "./samlForm";

const PEM_A = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----";
const PEM_B = "-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----";

function provider(extra: Partial<SamlProvider> = {}): SamlProvider {
  return {
    name: "entra",
    label: "Entra ID",
    enabled: true,
    idp_entity_id: "https://sts.windows.net/tenant/",
    sso_url: "https://login.microsoftonline.com/tenant/saml2",
    certificates: [PEM_A, PEM_B],
    certificate_details: [],
    sp_entity_id: "",
    entity_id: "https://noust.acme.test/api/auth/saml/metadata/entra",
    acs_url: "https://noust.acme.test/api/auth/saml/acs",
    metadata_url: "https://noust.acme.test/api/auth/saml/metadata/entra",
    subject_attribute: "",
    email_attribute: "email",
    name_attribute: "displayName",
    username_attribute: "",
    groups_attribute: "groups",
    clock_skew_seconds: 60,
    provisioning: "auto",
    default_role: "operator",
    default_team: null,
    default_team_role: null,
    link_by_email: true,
    allowed_domains: ["acme.test", "acme.es"],
    require_email_verified: false,
    assume_email_verified: true,
    require_amr: ["urn:oasis:names:tc:SAML:2.0:ac:classes:TimeSyncToken"],
    group_sync: true,
    group_mappings: [{ group: "ops", role: "operator", team: null, team_role: null, bind: null, project: null, environment: null }],
    created_at: 1,
    created_by: null,
    updated_at: 1,
    updated_by: null,
    ...extra,
  };
}

describe("the certificates box", () => {
  it("splits PEM blocks, however they are separated", () => {
    expect(splitCertificates(`${PEM_A}\n\n${PEM_B}\n`)).toEqual([PEM_A, PEM_B]);
    expect(splitCertificates(`junk ${PEM_A} more junk`)).toEqual([PEM_A]);
  });

  it("leaves text that is not PEM as one entry for the server to judge, and nothing as none", () => {
    expect(splitCertificates("MIICvDCCAaSgAwIBAgIU")).toEqual(["MIICvDCCAaSgAwIBAgIU"]);
    expect(splitCertificates("  \n ")).toEqual([]);
  });
});

describe("a SAML provider's form", () => {
  it("sends only the metadata when only the metadata was given", () => {
    const built = samlSettingsOf({ ...NEW_SAML_DRAFT, name: "entra", metadata: "<md:EntityDescriptor/>" });

    expect(built.settings).toMatchObject({ idp_metadata: "<md:EntityDescriptor/>", provisioning: "off" });
    expect(built.settings).not.toHaveProperty("idp_entity_id");
    expect(built.settings).not.toHaveProperty("sso_url");
    expect(built.settings).not.toHaveProperty("certificates");
  });

  it("reads a provider back and writes the same settings, so that what is on screen is what is saved", () => {
    const draft = samlDraftOf(provider());
    const built = samlSettingsOf(draft);

    expect(built.settings).toMatchObject({
      idp_entity_id: "https://sts.windows.net/tenant/",
      sso_url: "https://login.microsoftonline.com/tenant/saml2",
      certificates: [PEM_A, PEM_B],
      provisioning: "auto",
      default_role: "operator",
      link_by_email: true,
      allowed_domains: ["acme.test", "acme.es"],
      require_amr: ["urn:oasis:names:tc:SAML:2.0:ac:classes:TimeSyncToken"],
      group_sync: true,
      group_mappings: [{ group: "ops", role: "operator" }],
      clock_skew_seconds: 60,
    });
    expect(built.settings).not.toHaveProperty("idp_metadata");
  });

  it("clears the team when none is chosen, with its role", () => {
    const built = samlSettingsOf({ ...samlDraftOf(provider()), defaultTeam: "", defaultTeamRole: "owner" });

    expect(built.settings).toMatchObject({ default_team: "", default_team_role: "" });
  });

  it("points at the mappings line that is not a mapping", () => {
    const built = samlSettingsOf({ ...samlDraftOf(provider()), mappings: "ops=operator\nnonsense" });

    expect(built).toEqual({ settings: null, badLine: 2 });
  });

  it("does not send a clock tolerance that is not a number", () => {
    const built = samlSettingsOf({ ...samlDraftOf(provider()), clockSkew: "soon" });

    expect(built.settings).not.toHaveProperty("clock_skew_seconds");
  });
});
