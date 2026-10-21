/**
 * Strings of Settings > Single sign-on > Directories: the LDAP and Active Directory servers
 * people sign in against with the name and password they have there. What the server refuses
 * with, and what a test finds, is shown as it was written.
 */
export const ldapAdmin = {
  form: {
    createTitle: "Add a directory",
    editTitle: "Edit {name}",
    description:
      "The connection is encrypted and the certificate verified. A directory on a private network is allowed by naming its address in security.allow_private_hosts.",
    name: "Name",
    nameHelp: "A short name in lower case, such as corp or ad. It cannot change later.",
    label: "Label",
    labelHelp: "What the sign-in page says.",
    host: "Server",
    hostHelp: "The directory's name or address, without a scheme or a port.",
    port: "Port",
    tls: "Encryption",
    tlsLdaps: "ldaps: TLS from the first byte (port 636)",
    tlsStarttls: "StartTLS: upgrade the connection (port 389)",
    tlsNone: "None: only for a directory on this machine",
    caFile: "Certificate authority",
    caFileHelp: "The path of a PEM file with the authority to trust, when it is not one the system knows.",
    enabled: "Offer it on the sign-in page",
    mappingsLine: "Line {line} is not a mapping. Write GROUP=ROLE, GROUP=TEAM:ROLE or GROUP=ROLE@TEAM/PROJECT.",
    create: "Add directory",
    save: "Save directory",
    cancel: "Cancel",
    errorTitle: "The directory was not saved",
    service: {
      title: "The account that searches",
      bindDn: "Distinguished name",
      bindDnHelp: "Leave empty to search anonymously.",
      password: "Password",
      passwordHelp: "Kept apart from the settings and never shown again.",
      passwordKept: "A password is stored. Leave this empty to keep it.",
      clear: "Forget the stored password",
    },
    people: {
      title: "Finding people",
      userBase: "Search from",
      userBaseHelp: "Where people are, such as ou=people,dc=acme,dc=test.",
      userFilter: "Search",
      userFilterHelp: "{placeholder} is replaced by what the person types, escaped. It must find one entry.",
      subject: "Stable identifier",
      subjectHelp: "What identifies a person for good: entryUUID (OpenLDAP, 389, FreeIPA) or objectGUID (Active Directory). Never their name or e-mail.",
      email: "E-mail attribute",
      displayName: "Display name attribute",
      username: "Login name attribute",
      timeout: "Seconds one sign-in may take",
    },
    groups: {
      title: "Where groups are read",
      attribute: "Groups listed on the person",
      attributeHelp: "memberOf on Active Directory and on OpenLDAP with the overlay.",
      base: "Or search groups from",
      baseHelp: "When the person's entry does not list them.",
      filter: "Group search",
      filterHelp: "{placeholder} is replaced by the person's name. Such as {example}.",
    },
  },
  removeDialog: {
    title: "Remove {name}?",
    description: "Its password is forgotten and people linked to it can no longer sign in with it. Their accounts stay as they are.",
    action: "Remove directory",
  },
  savedToast: "Saved the directory {name}",
} as const;
