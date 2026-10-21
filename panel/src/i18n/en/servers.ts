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
  // Servers: monitoring, removal and the manual add flows

  settings: {
    linkedAddress: "Dials in: port {port} of this central",
    testedToast: "{name} answered in {latency} ms",
    testedDescription: "It runs Noust {version}.",
    testFailedToast: "{name} did not answer",
    twoFactor: {
      title: "Turn on two-factor sign-in first",
      link: "Set up two-factor authentication",
    },
    monitoring: {
      paused: "Monitoring paused",
      pausedWhy: "Monitoring paused: {reason}",
      silentSince: "Not answering since {when}",
      downSince: "Declared down, silent since {when}",
      pause: "Pause monitoring",
      resume: "Resume monitoring",
      pauseTitle: "Pause the monitoring of {name}?",
      pauseDescription: "While it is paused the central does not ask {name} how it is, does not count it as down and never announces it, so maintenance on it does not raise an alarm. What it last answered stays on screen. Resume it when you are done.",
      pauseAction: "Pause monitoring",
      reason: "Why",
      reasonHelp: "Shown beside the server, so whoever looks later knows it was on purpose.",
      pausedToast: "Monitoring of {name} paused",
      resumeFailedToast: "Monitoring of {name} could not be resumed",
    },
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
  // Adding a server that dials in (behind NAT)

  link: {
    title: "Add a server behind NAT",
    description: "It dials this central instead of the other way round: nothing about the server has to be reachable from outside.",
    steps: {
      invite: "Invite",
      code: "Link code",
      result: "Result",
    },
    status: {
      failed: "Could not read whether this central takes servers that dial in",
    },
    notReady: {
      title: "This central is not ready for servers that dial in",
      listLabel: "What is missing",
      run: "Prepare it from a terminal on this central, as root",
    },
    host: {
      label: "Address the server dials",
      description: "A name or IP address that reaches this central's SSH from the server's network, such as nas.example.com.",
      invalid: "Use a host name such as nas.example.com, or an IP address.",
    },
    ports: {
      label: "Ports",
      ssh: "SSH port",
      sshHelp: "The port this central's sshd listens on. Empty keeps {port}.",
      link: "Link port",
      linkHelp: "The loopback port of this central the server's link listens on. Empty gives it a free one.",
      invalid: "Use a port from 1 to 65535 for SSH and from 1024 to 65535 for the link.",
    },
    showCommand: "Show the command",
    again: "Show it again",
    inviteFailed: "Could not invite the server",
    command: {
      whereTitle: "Run it on the other server: the one you are adding",
      where: "Not here on {central}. Open a terminal on the server you are adding, as root, and run the command below there.",
      intro: "On {name}, as root, run this command. It keeps a connection to this central open and prints a link code on one line: copy it for the next step.",
      label: "Command to run on {name}",
      copy: "Copy command",
      hostKey: "It carries this central's SSH host key, which {name} pins: {fingerprint}. Compare it with ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub here.",
      expires: "Valid until {when}.",
      missing: "Name the server, say what address it dials and show its command first.",
      safeTitle: "Why this is safe",
      safeBody:
        "The key it makes can only listen on one loopback port of this central, from an account with no shell (noust-link): it cannot run anything here. {name} gets no shell on this central and this central gets none on {name}: it talks to {name} only through its API, with a token {name} issues and can revoke with noust fleet unlink.",
    },
    code: {
      intro: "Paste what the command printed on {name}.",
      label: "Link code",
      description: "It starts with noust-link:v1: and carries a token for {name}'s console: treat it like a password. It is sent once and not shown again.",
      problem: {
        empty: "Paste the link code the command printed on the server.",
        joinCode: "That is a join code, for a server this central reaches over SSH. The link code is the line noust fleet link printed on the server, starting with noust-link:v1:.",
        apiToken: "That is an API token, not a link code. The link code is the line noust fleet link printed on the server, starting with noust-link:v1:.",
        consoleToken: "That looks like a console access token (noust_…), not a link code. The link code is the line noust fleet link printed on the server, starting with noust-link:v1:.",
        newer: "This link code is from a newer Noust than this central. Update Noust on this central, then paste it again.",
        wrongPrefix: "A link code starts with noust-link:v1:. Copy the whole line noust fleet link printed on the server.",
        unreadable: "This starts like a link code but does not read as one: copy the whole line again, without cutting it.",
      },
      summary: "For {node}: its console on port {console}, Noust {version}.",
      otherNode: "This code is for a server called {node}, not {name}. This central would refuse it.",
      otherCentral: "This code was made for the central {central}, not for this one ({name}). This central would refuse it.",
      waiting: "Waiting for {name} to connect: it can take up to 30 seconds.",
      submit: "Add server",
      elevationNote: "Adding a server asks you to confirm it's you.",
    },
    result: {
      tryAgain: "Back to the link code",
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
