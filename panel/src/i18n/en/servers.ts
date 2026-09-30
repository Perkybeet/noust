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

  reachability: {
    locked: "Locked",
  },

  // -------------------------------------------------------------------------------------
  // What a hub says on the Fleet, and a server not read yet

  fleet: {
    notRead: "Not read",
    hub: {
      title: "This central deploys nothing itself",
      description:
        "It is a hub: applications, sites, certificates, databases and backups live on the servers it manages. Open one from its row, or choose it above.",
      noServers: "Add a server first: a hub's applications all live on its servers.",
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
      address: "Tunnel account and address",
      status: "Status",
      version: "Version",
      access: "This central may",
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
    description: "This central never logs in to it: the server authorizes this central, once, from its own terminal.",
    steps: {
      authorize: "Authorize",
      join: "Join",
      result: "Result",
    },
    cancel: "Cancel",
    back: "Back",
    next: "I ran it: continue",
    done: "Done",
    name: {
      label: "Name",
      description: "How this console will call the server: 1 to 32 lower-case letters, digits and dashes, such as web-2.",
      invalid: "Use 1 to 32 lower-case letters, digits and dashes, starting and ending with a letter or digit.",
      showCommand: "Show the command",
      again: "Show it again",
      keyFailed: "Could not prepare the central's key",
    },
    access: {
      label: "This central may",
      description: "The most this central may do on it. The server enforces it, whatever this central asks.",
      adminHint: "Deploy, configure and delete, as on the server itself",
      deployHint: "Operate, update and roll back its applications",
      readHint: "See everything, change nothing",
    },
    authorize: {
      whereTitle: "Run it on the other server: the one you are adding",
      where: "Not here on {central}. Open a terminal on the server you are adding, as root, and run the command below there.",
      intro: "On {name}, as root, run this command. It prints a join code on one line: copy it for the next step.",
      commandLabel: "Command to run on {name}",
      copy: "Copy command",
      missing: "Name the server and show its command first.",
      safeTitle: "Why this is safe",
      safeBody:
        "The key in this command can only forward {name}'s console port to this central, from an account with no shell (noust-tunnel). It cannot run anything on {name}, and {name} can revoke it at any time with noust fleet deauthorize. The central talks to {name} only through its API, with a token {name} issues and can revoke.",
    },
    join: {
      intro: "Paste what the command printed on {name}, and where the central reaches its SSH.",
      codeLabel: "Join code",
      codeDescription: "It starts with noust-join:v1: and carries a token for {name}'s console: treat it like a password. It is sent once and not shown again.",
      problem: {
        empty: "Paste the join code the command printed on the server.",
        apiToken: "That is an API token, not a join code. The join code is the line noust fleet authorize printed on the server, starting with noust-join:v1:.",
        consoleToken:
          "That looks like a console access token (noust_…), not a join code. The join code is the line noust fleet authorize printed on the server, starting with noust-join:v1:.",
        newer: "This join code is from a newer Noust than this central. Update Noust on this central, then paste it again.",
        wrongPrefix: "A join code starts with noust-join:v1:. Copy the whole line noust fleet authorize printed on the server.",
        unreadable: "This starts like a join code but does not read as one: copy the whole line again, without cutting it.",
      },
      summaryTitle: "Join code read",
      summary: "For {node}: its tunnel account {user} on SSH port {port}, its console on port {console}, Noust {version}.",
      otherCentral: "This code was made for the central {central}, not for this one ({name}). The server would refuse it.",
      otherKey: "This code was made for another key than the one this central showed in the first step. Run the command from the first step again.",
      rootAccount: "This server lets the central in as root. A dedicated tunnel account (noust-tunnel, the default) is safer: run the command again without --ssh-user root.",
      addressLabel: "SSH address",
      addressDescription: "The server's host name or address. The account and port come from the join code; add them as user@host:port only to change them.",
      addressDescriptionFrom: "The server's host name or address. The central connects as {user} on port {port}, from the join code.",
      addressInvalid: "Write it as host, user@host or user@host:port.",
      submit: "Add server",
      elevationNote: "Adding a server asks you to confirm it's you.",
    },
    result: {
      addedTitle: "{name} is part of the fleet",
      addedBody: "The central pinned its host key, opened the tunnel and checked the token.",
      status: "Status",
      version: "Version",
      tunnel: "Tunnel account and address",
      access: "This central may",
      accessUnknown: "Not published yet",
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
  // Settings > Central

  central: {
    documentTitle: "Central settings",
    fromTerminal: "From a terminal on the central",
    identity: {
      title: "This central",
      description: "The Noust that manages the fleet, and what it does itself.",
      name: "Name",
      role: "Role",
      roleServer: "Manages servers and deploys applications itself",
      roleHub: "A hub: manages servers, deploys nothing itself",
      servers: "Servers",
      serverCount: { one: "{count} server", other: "{count} servers" },
    },
    seal: {
      title: "Sealed secrets",
      description: "The keys and tokens that reach your servers, encrypted with a passphrase that is stored nowhere.",
      locked: "Locked",
      unlocked: "Unlocked",
      notSealed: "Not sealed",
      unlockedBody: "Sealed, and unlocked since this central last started: its servers are within reach. After a restart it asks for the passphrase again.",
      notSealedBody:
        "The keys and tokens are protected by the machine's own file permissions only. Seal them to keep them unreadable if this machine's disk is copied.",
    },
  },

  // -------------------------------------------------------------------------------------
  // A hub

  hub: {
    refusedTitle: "Not available on a hub",
    refusedHint: "This central is a hub: it deploys nothing itself. Open a server of the fleet and do it there.",
  },
} as const;
