/**
 * Strings of Settings > Single sign-on > SAML providers: the SAML 2.0 identity providers people
 * sign in with (Entra ID, Okta, Keycloak, ADFS). What the server refuses with, and what a test
 * finds, is shown as it was written.
 */
export const samlAdmin = {
  certificate: {
    expired: "Expired",
  },
  plainHttpTitle: "This console is not served over HTTPS",
  plainHttp:
    "This console is reached over plain HTTP. A SAML provider answers with a form posted from its own site, which a browser only sends the sign-in cookie with over HTTPS, so no SAML sign-in can finish until the console is served over TLS.",
  form: {
    createTitle: "Add a SAML provider",
    editTitle: "Edit {name}",
    description:
      "Assertions must be signed by one of the certificates below. Encrypted assertions are refused: the connection is already encrypted.",
    name: "Name",
    nameHelp: "A short name in lower case, such as corp or entra. It cannot change later.",
    label: "Label",
    labelHelp: "The name on its sign-in button.",
    enabled: "Offer it on the sign-in page",
    mappingsLine: "Line {line} is not a mapping. Write GROUP=ROLE, GROUP=TEAM:ROLE or GROUP=ROLE@TEAM/PROJECT.",
    create: "Add provider",
    save: "Save provider",
    cancel: "Cancel",
    errorTitle: "The provider was not saved",
    register: {
      title: "Register this server with the provider",
      description: "Give the provider these, or its metadata address, when it asks for a service provider.",
      entityId: "Entity ID",
      acs: "Response URL (assertion consumer service)",
      metadata: "Metadata address",
    },
    idp: {
      title: "The identity provider",
      metadata: "Its metadata",
      needed: "Give the metadata, or the entity ID, the sign-on address and a certificate.",
      metadataHelp: "Paste the XML the provider publishes: it fills the three settings below, and what you type beside it wins.",
      entityId: "Entity ID",
      entityIdHelp: "What its assertions name as their issuer.",
      ssoUrl: "Sign-in address",
      ssoUrlHelp: "Where the browser is sent to sign in (the HTTP-Redirect binding).",
      certificates: "Signing certificates",
      certificatesHelp: "In PEM, one after the other. Give the old and the new during a rollover; replacing the list replaces them all.",
    },
    server: {
      title: "How this server presents itself",
      entityId: "Entity ID of this server",
      entityIdHelp: "Empty derives it from this console's address, which is what most providers accept.",
    },
    people: {
      title: "Finding people",
      subject: "Attribute that identifies a person",
      subjectHelp: "Empty uses the NameID, which should be a persistent identifier. Never an e-mail that can be reassigned.",
      email: "E-mail attribute",
      displayName: "Display name attribute",
      username: "Login name attribute",
      groups: "Groups attribute",
      groupsHelp: "Where the response lists the groups a person is in.",
      skew: "Tolerated clock difference (seconds)",
    },
    contexts: "Authentication classes required",
    contextsHelp: "One of these must be reported, such as urn:oasis:names:tc:SAML:2.0:ac:classes:TimeSyncToken. Empty asks for none.",
  },
  removeDialog: {
    title: "Remove {name}?",
    description: "People linked to it can no longer sign in with it. Their accounts stay as they are.",
    action: "Remove provider",
    toast: "Removed the SAML provider {name}",
  },
  savedToast: "Saved the SAML provider {name}",
} as const;
