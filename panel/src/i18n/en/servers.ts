/** Strings of the fleet: the Fleet page, Settings > Servers, the central's lock and its hub role. */
export const servers = {
  nav: {
    fleet: { label: "Fleet", keywords: "servers nodes central vps machines", goTo: "Go to fleet" },
    settingsTab: {
      label: "Servers",
      keywords: "fleet nodes central ssh tunnel add server join code",
      command: "Servers settings",
    },
  },

  /** The row of the machine the console runs on, among the servers it manages. */
  thisServer: "This server",
  hubBadge: "Hub",

  reachability: {
    locked: "Locked",
  },

  // -------------------------------------------------------------------------------------
  // The Fleet page

  fleet: {
    title: "Fleet",
    description: "Every server this central manages, side by side: whether it answers, what it runs and what needs you.",
    addServer: "Add a server",
    manageServers: "Manage servers",
    serversTitle: "Servers",
    serversDescription: "Readings refresh every 15 seconds; applications, certificates and units every minute.",
    tableCaption: "Servers of this fleet",
    loadingLabel: "the fleet",
    column: {
      server: "Server",
      reachability: "Reachability",
      version: "Version",
      cpu: "CPU",
      memory: "Memory",
      disk: "Disk",
      apps: "Applications",
      units: "Units",
      certificates: "Certificates",
      lastSeen: "Last seen",
    },
    versionMismatch: "Other version",
    versionMismatchLabel: "Runs {version}; this central runs {central}",
    appsRunning: { one: "{count} running", other: "{count} running" },
    appsFailed: { one: "{count} failed", other: "{count} failed" },
    unitsFailed: { one: "{count} failed", other: "{count} failed" },
    unitsNoneFailed: "None failed",
    certsExpiring: { one: "{count} expiring", other: "{count} expiring" },
    certsNoneExpiring: "None expiring",
    notRead: "Not read",
    now: "Now",
    never: "Never",
    hubNoApps: "Deploys nothing",
    open: "Open {name}",
    empty: {
      title: "No servers in this fleet yet",
      description:
        "A fleet is several Noust servers run from one console, this central's. The central reaches each server through an SSH tunnel to its console, with a key that can forward that one port and nothing else, and drives it through its own API.",
      action: "Add a server",
    },
    hub: {
      title: "This central deploys nothing itself",
      description:
        "It is a hub: applications, sites, certificates, databases and backups live on the servers it manages. Pick a server below to open them there.",
      noServers: "Add a server first: a hub's applications all live on its servers.",
    },
    attention: {
      title: "Needs attention",
      description: "Problems on every server, worst first. Each opens on the server it is about.",
      allClear: { one: "Nothing needs attention on the {count} server.", other: "Nothing needs attention on any of the {count} servers." },
      onServer: "On {server}",
      unreachable: "The central cannot reach this server",
      refused: "This server refused the central's token",
      unreachableFix:
        "Check that the server is up and answers SSH at {address}, and that its host key has not changed. What ssh said:",
      refusedFix:
        "The server no longer accepts this central's fleet token. Run noust fleet authorize on it again, then remove and add it here with the new join code.",
      machineFailed: "Could not read this server's machine",
      unchecked: "Could not check its {source}:",
      sources: {
        apps: "applications",
        deploys: "deployments",
        certificates: "certificates",
        units: "units",
      },
      testAgain: "Test again",
    },
  },

  // -------------------------------------------------------------------------------------
  // Settings > Servers

  settings: {
    documentTitle: "Servers settings",
    title: "Servers",
    description:
      "The servers this central manages. It reaches each through an SSH tunnel to the server's console, with a key that can forward that one port and never run a command.",
    loadingLabel: "servers",
    tableCaption: "Servers this central manages",
    column: {
      name: "Name",
      address: "SSH address",
      status: "Status",
      version: "Version",
      lastSeen: "Last seen",
    },
    addServer: "Add a server",
    test: "Test",
    testLabel: "Test {name}",
    testedToast: "{name} answered in {latency} ms",
    testedDescription: "It runs Noust {version}.",
    testFailedToast: "{name} did not answer",
    open: "Open",
    openLabel: "Open {name}",
    remove: "Remove",
    removeLabel: "Remove {name}",
    never: "Never",
    empty: {
      title: "No servers yet",
      description:
        "Add a server to manage it from this console. You authorize the central on the server itself, where you are already root; the central never gets a shell there.",
    },
    twoFactor: {
      title: "Turn on two-factor sign-in first",
      description:
        "This central refuses to add its first server without it: whoever signs in here reaches every server it manages.",
      link: "Set up two-factor authentication",
    },
    fromTerminal: "From a terminal",
    removeDialog: {
      title: "Remove {name}?",
      description:
        "The central closes its tunnel to {name} and forgets its key, token and pinned host key. When {name} answers, the central first revokes its token there, so the key it leaves behind opens nothing.",
      unreachable: "If {name} cannot be reached, run this on it, as root, to remove the central's key and revoke its token:",
      unreachableUnknown: "If {name} cannot be reached, Noust will say what to run on it to finish.",
      action: "Remove server",
      removedToast: "Removed {name}",
      resultTitle: "Removed {name}",
      resultDescription: "What the central did, in its own words:",
    },
  },

  // -------------------------------------------------------------------------------------
  // Adding a server

  add: {
    title: "Add a server",
    stepOf: "Step {step} of {total}",
    steps: {
      authorize: "Authorize",
      join: "Join",
      result: "Result",
    },
    progressLabel: "Progress",
    stepDone: "{name}, done",
    stepCurrent: "{name}, current step",
    stepTodo: "{name}, not yet",
    cancel: "Cancel",
    back: "Back",
    next: "I ran it: next",
    done: "Done",
    name: {
      label: "Name",
      description: "How this console will call the server: 1 to 32 lower-case letters, digits and dashes, such as web-2.",
      invalid: "Use 1 to 32 lower-case letters, digits and dashes, starting and ending with a letter or digit.",
      showCommand: "Show the command",
      keyFailed: "Could not prepare the central's key",
    },
    authorize: {
      intro: "On {name}, as root, run this command. It prints a join code on one line: copy it for the next step.",
      commandLabel: "Command to run on {name}",
      copy: "Copy command",
      safeTitle: "Why this is safe",
      safeBody:
        "The key in this command can only forward {name}'s console port to this central. It cannot open a shell or run anything on {name}, and {name} can revoke it at any time with noust fleet deauthorize. The central talks to {name} only through its API, with a token {name} issues and can revoke.",
    },
    join: {
      intro: "Paste what the command printed on {name}, and where the central reaches its SSH.",
      codeLabel: "Join code",
      codeDescription: "It carries a token for {name}'s console: treat it like a password. It is sent once and not shown again.",
      addressLabel: "SSH address",
      addressDescription:
        "As user@host or user@host:port, such as root@web2.example.com. The user and port default to the ones in the join code.",
      addressInvalid: "Write it as host, user@host or user@host:port.",
      submit: "Add server",
      elevationNote: "Adding a server asks you to confirm it's you.",
    },
    result: {
      addedTitle: "{name} is part of the fleet",
      addedBody: "The central pinned its host key, opened the tunnel and checked the token.",
      status: "Status",
      version: "Version",
      address: "SSH address",
      notReported: "Not reported yet",
      open: "Open {name}",
      failedTitle: "Could not add {name}",
      twoFactorLink: "Set up two-factor authentication",
      tryAgain: "Back to the join code",
    },
    addedToast: "Added {name}",
  },

  // -------------------------------------------------------------------------------------
  // The central's sealed secrets

  lock: {
    title: "This central is locked",
    description:
      "Its secrets are sealed at rest: the keys and tokens that reach your servers are encrypted under a passphrase that is written nowhere. Until you unlock them, nothing reaches the servers.",
    passphraseLabel: "Passphrase",
    unlock: "Unlock",
    wrongPassphrase: "That passphrase does not open this central's secrets. Check it and try again.",
    failedTitle: "Could not unlock the central",
    continueLocked: "Continue without unlocking",
    continueNote: "The console still works on this machine; its servers stay out of reach until you unlock.",
    lostPassphrase: "Nobody can recover a lost passphrase. Without it, every server has to be authorized again.",
    unlockedToast: "Unlocked: the servers are within reach again",
    fromTerminal: "Or from a terminal on the central",
    bannerTitle: "This central is locked",
    bannerDescription: "Its servers are out of reach until you unlock its sealed secrets.",
    bannerAction: "Unlock",
    dialogTitle: "Unlock this central",
  },

  // -------------------------------------------------------------------------------------
  // A hub

  hub: {
    refusedTitle: "Not available on a hub",
    refusedHint: "This central is a hub: it deploys nothing itself. Open a server of the fleet and do it there.",
  },
} as const;
