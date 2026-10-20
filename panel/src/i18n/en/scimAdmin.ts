/**
 * Strings of Settings > Single sign-on > Provisioning: the identity providers that keep the
 * roster of who has an account and retire people as they leave (SCIM). What the server refuses
 * with is shown as it was written.
 */
export const scimAdmin = {
  title: "Provisioning (SCIM)",
  description:
    "An identity provider that keeps the roster: it creates the account of a new person, switches it off when they leave, and gives roles by group as it pushes them. It does not sign anybody in; the sign-in providers above do.",
  loadingLabel: "provisioning sources",
  tableCaption: "Provisioning sources",
  add: "Add a source",
  emptyTitle: "No provisioning source",
  emptyDescription:
    "Add one to let Microsoft Entra ID, Okta or another provider create accounts and switch them off when a person leaves.",
  endpoint: "SCIM endpoint",
  endpointHelp: "Give the provider this address and the source's token.",
  column: {
    source: "Source",
    state: "State",
    people: "People",
    starts: "New accounts",
    lastUsed: "Last used",
  },
  state: {
    enabled: "Enabled",
    disabled: "Disabled",
  },
  neverUsed: "Never",
  editText: "Edit",
  removeText: "Remove",
  form: {
    createTitle: "Add a provisioning source",
    editTitle: "Edit {name}",
    description:
      "The provider authenticates with a token of its own, shown once. A new account waits for the person's first sign-in through one of the providers above.",
    name: "Name",
    nameHelp: "A short name in lower case, such as entra or okta. It cannot change later.",
    label: "Label",
    labelHelp: "What people read about it.",
    enabled: "Accept its token",
    enabledHelp: "Off, the provider is refused; the accounts it made stay as they are.",
    rotate: "Issue a new token",
    rotateHelp: "The old token stops working at once, so the provider must be given the new one.",
    mappingsLine: "Line {line} is not a mapping. Write GROUP=ROLE, GROUP=TEAM:ROLE or GROUP=ROLE@TEAM/PROJECT.",
    create: "Add source",
    save: "Save source",
    cancel: "Cancel",
    errorTitle: "The source was not saved",
  },
  token: {
    title: "Give the provider this token",
    description: "{name} authenticates with it. This is the only time it is shown; only its last characters are kept.",
    warning: "Copy it now. If it is lost, issue a new one: the old one stops working.",
    tokenLabel: "Token for {name}",
    copy: "Copy token",
    endpointLabel: "SCIM endpoint",
    done: "Done",
  },
  remove: {
    title: "Remove {name}?",
    description: "Its token stops working and the provider can no longer create or switch off accounts. The accounts it made stay as they are.",
    action: "Remove source",
    toast: "Removed the source {name}",
  },
  savedToast: "Saved the source {name}",
} as const;
