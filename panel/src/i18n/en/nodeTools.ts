/**
 * Strings of what a server's menu in the Fleet can do beyond the basics: how the central connects to it
 * (its wait, a bastion), its inventory (to take it to another central) and the machine it is at a
 * cloud provider. Addresses, fingerprints, keys and the servers' own words are data, never translated.
 */
export const nodeTools = {
  menu: {
    connection: "Connection options",
    exportInventory: "Export its inventory",
    applyInventory: "Apply an inventory",
    linkMachine: "Link to a cloud machine",
  },
  connection: {
    title: "How this central connects to {name}",
    description:
      "The wait for the connection, and a bastion to go through when the server has no address this central can reach. The change is tried before it is kept: when {name} does not answer, the old options stay.",
    loading: "Reading how this central connects to {name}",
    loadFailed: "Could not read how {name} is connected to",
    timeoutLabel: "Wait for the connection",
    timeoutDescription: "Seconds, from 1 to 300. Empty is the default, {seconds} seconds.",
    timeoutInvalid: "Write a whole number of seconds from 1 to 300.",
    bastionToggle: "Connect through a bastion",
    bastionToggleHelp:
      "This central asks the bastion for one stream to the server. The server's own host key is still checked end to end, and the bastion carries bytes it cannot read.",
    bastionAddressLabel: "Bastion",
    bastionAddressDescription: "As user@host or user@host:port.",
    bastionAddressInvalid: "Write the bastion as user@host or user@host:port.",
    bastionKeyLabel: "Its host key",
    bastionKeyDescription:
      "Its fingerprint (SHA256:...) as the bastion prints it, or its ssh-ed25519 key line. This central never learns a host key on first use.",
    bastionKeyKept: "Leave it empty to keep the key already pinned for {address}.",
    bastionKeyRequired: "Give the bastion's fingerprint or host key.",
    pinned: "Host key pinned: {fingerprint}",
    authorizedTitle: "Authorize this central on the bastion",
    authorizedDescription:
      "Add this line to the authorized_keys of {user} on the bastion. It allows one stream to {name} and nothing else: no shell, no other destination.",
    authorizedCopy: "the line for the bastion",
    save: "Save and test",
    cancel: "Cancel",
    close: "Close",
    failed: "Could not change how {name} is connected to",
    saved: "{name} answers with these options",
  },
  inventory: {
    exportDone: "Inventory of {name} downloaded",
    exportFailed: "Could not export the inventory of {name}",
    applyTitle: "Apply an inventory to {name}",
    applyDescription:
      "Another central exported what it knew about this server (noust node export). Its labels, placements and the rest are recorded here. The file holds no secret.",
    fileLabel: "Inventory file",
    fileDescription: "The JSON file noust node export wrote.",
    fileMissing: "Choose the inventory file.",
    fileUnreadable: "That file could not be read as an inventory.",
    includeSsh: "Also apply how that central reached the server",
    includeSshHelp: "Its wait and its bastion. A path that worked from there may not exist from here.",
    apply: "Apply",
    cancel: "Cancel",
    close: "Close",
    failed: "Could not apply the inventory to {name}",
    nothing: "The inventory had nothing to record.",
    appliedLegend: "Recorded",
    skippedLegend: "Left out",
    part: {
      labels: "Labels",
      placements: "Applications on several servers",
      monitoring: "Paused monitoring",
      ssh: "Connection options",
      cloud: "Machine at a provider",
    },
  },
  link: {
    title: "Link {name} to its machine at a provider",
    description:
      "The provider is asked whether the machine exists and is where {name} is. Nothing is changed at the provider. Afterwards this central shows whether it is on, can power it on and, on request, delete it.",
    accountLabel: "Cloud account",
    noAccounts: "No cloud account yet. Add one in Settings, Cloud.",
    machineLabel: "Machine id",
    machineDescription: "The provider's id for the machine, as its console shows it.",
    machineInvalid: "Write the machine's id.",
    force: "Link it even if the machine is not at this server's address",
    forceHelp: "Only when the server answers on another address than the one the provider reports.",
    link: "Link",
    cancel: "Cancel",
    failed: "Could not link {name}",
    linked: "{name} is linked to machine {machine}",
  },
} as const;
