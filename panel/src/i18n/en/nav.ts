/** The console's destinations (app/nav.ts): names, palette search words, shortcut labels. */
export const nav = {
  landmarks: {
    main: "Main",
    appSections: "Application sections",
    settingsSections: "Settings sections",
  },
  /** The sidebar's headings on a fleet: every server at once, then the server on screen. */
  groups: {
    fleet: "Fleet",
    server: "Server",
  },
  /** Settings split by whose they are, on a fleet (features/settings/SettingsShell.tsx). */
  settingsGroups: {
    serverLabel: "Server",
    central: "Central {name}",
    serverDescription: "How Noust runs on {name}. These follow the server you choose.",
    centralDescription: "These are the central's, {name}: they stay the same whichever server you choose.",
    nodeNoteTitle: "Sign-in, two-factor and API tokens of {node} are managed on {node}",
    nodeNote:
      "{node} refuses this central on them on purpose: a central that was taken over could otherwise lock you out of every server. Sign in to the console of {node}, or run these on {node}:",
    nodeNoteCommands: "On {node}",
  },
  /** After a destination's name, for assistive technology: "Services 1 failed". */
  failed: { one: "{count} failed", other: "{count} failed" },
  overview: { label: "Overview", keywords: "home dashboard health", goTo: "Go to overview" },
  apps: { label: "Applications", keywords: "apps sites deploy", goTo: "Go to applications" },
  databases: { label: "Databases", keywords: "mysql postgres redis mongodb sql", goTo: "Go to databases" },
  services: { label: "Services", keywords: "systemd units daemons" },
  cron: { label: "Cron", keywords: "schedule timers jobs" },
  domains: { label: "Domains and certificates", keywords: "ssl tls https certbot nginx apache sites" },
  backups: { label: "Backups", keywords: "restore snapshots" },
  activity: { label: "Activity", keywords: "audit log jobs history deployments" },
  server: { label: "Server", keywords: "machine health cpu memory disk processes monitor" },
  settings: { label: "Settings", keywords: "preferences configuration", goTo: "Go to settings" },
  appTabs: {
    overview: { label: "Overview" },
    deployments: { label: "Deployments", keywords: "deploys builds history rollback" },
    logs: { label: "Logs", keywords: "journal output" },
    metrics: { label: "Metrics", keywords: "cpu memory charts" },
    environment: { label: "Environment", keywords: "env variables secrets" },
    domains: { label: "Domains", keywords: "certificate ssl www" },
    settings: { label: "Settings", keywords: "webhook source build port delete" },
  },
  settingsTabs: {
    general: { label: "General", keywords: "apps directory web server email language", command: "General settings" },
    security: { label: "Security", keywords: "two-factor 2fa totp sessions lockout", command: "Security settings" },
    notifications: {
      label: "Notifications",
      keywords: "alerts email slack webhook channels",
      command: "Notifications settings",
    },
    integrations: {
      label: "Integrations",
      keywords: "github app repositories push pull request previews",
      command: "Integrations settings",
    },
    tokens: { label: "API tokens", keywords: "automation ci scope", command: "API tokens" },
    about: { label: "About", keywords: "version update", command: "About settings" },
    central: { label: "Central", keywords: "seal passphrase unlock hub role fleet", command: "This central's settings" },
  },
} as const;
