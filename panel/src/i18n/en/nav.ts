/** The console's destinations (app/nav.ts): names, palette search words, shortcut labels. */
export const nav = {
  landmarks: {
    main: "Main",
    appSections: "Application sections",
    settingsSections: "Settings sections",
    teamSections: "Team sections",
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
  /** After Activity's name, for assistive technology: "Activity 2 running". */
  running: { one: "{count} running", other: "{count} running" },
  overview: { label: "Overview", keywords: "home dashboard health", goTo: "Go to overview" },
  projects: { label: "Projects", keywords: "teams environments production staging organise group workspace", goTo: "Go to projects" },
  apps: { label: "Applications", keywords: "apps sites deploy", goTo: "Go to applications" },
  catalog: { label: "Catalog", keywords: "recipes templates one-click marketplace store install apps", goTo: "Go to the catalog" },
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
  teamTabs: {
    overview: { label: "Overview" },
    members: { label: "Members", keywords: "people roles invite owner access" },
    notifications: { label: "Notifications", keywords: "alerts slack ntfy telegram webhook channels notices" },
    settings: { label: "Settings", keywords: "rename delete name" },
  },
  settingsTabs: {
    general: { label: "General", keywords: "apps directory web server email language", command: "General settings" },
    security: { label: "Security", keywords: "two-factor 2fa totp sessions lockout", command: "Security settings" },
    notifications: {
      label: "Notifications",
      keywords: "alerts email slack webhook channels",
      command: "Notifications settings",
    },
    alerts: {
      label: "Alerts",
      keywords: "alert rules threshold cpu memory disk firing monitor",
      command: "Alerts settings",
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
  /** Screens with no name of their own anywhere else: the account's and the controls' (app/nav.ts). */
  screens: {
    account: { label: "Profile and password", keywords: "me my user profile password name email", command: "Your account" },
    accountSecurity: {
      label: "Sign-in security",
      keywords: "two-factor 2fa authenticator totp passkeys sessions sign in",
      command: "Sign-in security",
    },
    controls: { label: "Controls", keywords: "compliance frameworks ens security profile policy", command: "Controls settings" },
    teams: { keywords: "people members roles organise group workspace" },
    terminal: { keywords: "shell ssh console command line" },
    files: { keywords: "browse edit upload download folder" },
    diagnose: { keywords: "troubleshoot why down broken health check" },
  },
  /** What these screens were called before the 4.0 navigation: still search words in the palette (RENAMED). */
  renamed: {
    domains: "Domains and certificates",
    notifications: "Notifications",
    alerts: "Alerts",
    console: "Console access",
    about: "About",
    access: "Roles and access",
    compliance: "Compliance",
    probes: "Probes",
    logShipping: "Log shipping",
    security: "Security",
  },
} as const;
