/** The console's destinations (app/nav.ts): names, palette search words, shortcut labels. */
export const nav = {
  landmarks: {
    main: "Main",
    appSections: "Application sections",
    settingsSections: "Settings sections",
    teamSections: "Team sections",
    accountSections: "Account sections",
    projectSections: "Project sections",
    overviewSections: "Overview sections",
    serverSections: "Server sections",
    alertsSections: "Alerts sections",
    catalogSections: "Catalog sections",
  },
  /** The titles of the groups of the main bar, drawn when it is one bar (the double bar separates them by space). */
  groups: {
    runs: "What runs",
    reach: "How it is reached",
    happens: "What is happening",
    safe: "Kept safe",
    where: "Where it runs",
    manage: "How it is managed",
  },
  /** What an application's sections are for: the titles of the groups of its second column. */
  intents: { use: "Use", configure: "Configure", fix: "Fix", danger: "Danger" },
  /** The main bar's own controls. */
  rail: {
    fold: "Fold the bar",
    unfold: "Unfold the bar",
  },
  /** The choice between the double bar and one bar, drawn in the bar itself. */
  presentation: {
    label: "Navigation",
    double: "Double bar",
    single: "One bar",
  },
  /** The bar at the foot of a phone: four areas and the rest. */
  bottomBar: {
    label: "Areas",
    more: "More",
  },
  /** The switch beside a breadcrumb that lists its siblings. */
  crumbs: {
    switch: "Switch {name}",
  },
  /**
   * Settings split by whose they are (features/settings/SettingsShell.tsx): the titles of its
   * groups, and what the page says about whose the sections are on a fleet.
   */
  settingsGroups: {
    central: "Central {name}",
    serverDescription: "How Noust runs on {name}. These follow the server you choose.",
    centralDescription: "These are the central's, {name}: they stay the same whichever server you choose.",
    nodeNoteTitle: "Sign-in, two-factor and API tokens of {node} are managed on {node}",
    nodeNote:
      "{node} refuses this central on them on purpose: a central that was taken over could otherwise lock you out of every server. Sign in to the console of {node}, or run these on {node}:",
    nodeNoteCommands: "On {node}",
    titles: {
      thisServer: "This server",
      monitoring: "Monitoring",
      developers: "Developers",
      people: "People and access",
      governance: "Governance",
      fleet: "Fleet",
    },
  },
  /** After a destination's name, for assistive technology: "Services 1 failed". */
  failed: { one: "{count} failed", other: "{count} failed" },
  /** After Activity's name, for assistive technology: "Activity 2 running". */
  running: { one: "{count} in progress", other: "{count} in progress" },
  /** After a destination's name: "Domains 2 certificates need renewing". */
  expiring: { one: "{count} certificate needs renewing", other: "{count} certificates need renewing" },
  overview: { label: "Home", keywords: "overview dashboard health", goTo: "Go to home" },
  projects: { label: "Projects", keywords: "teams environments production staging organise group workspace", goTo: "Go to projects" },
  apps: { label: "Applications", keywords: "apps sites deploy", goTo: "Go to applications" },
  catalog: { label: "Catalog", keywords: "catalog catalogue recipes templates one-click marketplace store install apps", goTo: "Go to the catalog" },
  databases: { label: "Databases", keywords: "mysql postgres redis mongodb sql", goTo: "Go to databases" },
  cron: { label: "Scheduled tasks", keywords: "cron schedule timers jobs", goTo: "Go to scheduled tasks" },
  domains: { label: "Domains", keywords: "ssl tls https certbot nginx apache sites certificates dns", goTo: "Go to domains" },
  backups: { label: "Backups", keywords: "restore snapshots", goTo: "Go to backups" },
  activity: { label: "Activity", keywords: "jobs history deployments", goTo: "Go to activity" },
  alerts: { label: "Alerts", goTo: "Go to alerts" },
  logs: { goTo: "Go to logs" },
  server: { label: "Servers", keywords: "server machine health cpu memory disk processes monitor", goTo: "Go to servers" },
  settings: { label: "Settings", keywords: "preferences configuration", goTo: "Go to settings" },
  /** The fleet's views, each the face of an area of the bar (features/nav/fleet.ts). */
  fleetBar: {
    overview: { label: "Overview" },
    servers: { label: "Servers" },
    apps: { label: "Applications" },
    certificates: { label: "Certificates" },
    backups: { label: "Backups" },
    updates: { label: "Updates", goTo: "Go to the fleet's updates" },
    activity: { label: "Activity" },
  },
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
    general: { label: "General", command: "General settings" },
    notifications: {
      label: "Channels",
      keywords: "notifications alerts email slack webhook channels where messages go",
      command: "Channels settings",
    },
    alerts: {
      label: "Alert rules",
      keywords: "alerts alert rules threshold cpu memory disk firing monitor when to warn",
      command: "Alert rules settings",
    },
    integrations: {
      label: "Integrations",
      keywords: "github app repositories push pull request previews",
      command: "Integrations settings",
    },
    probes: {
      label: "Probes and status page",
      keywords: "probes uptime monitor status page external check address http outage ping website down",
      command: "Probes and status page settings",
    },
    logShipping: {
      label: "Logs and metrics export",
      keywords: "log shipping logs metrics prometheus scrape loki axiom grafana fluent bit syslog opentelemetry otlp ship forward destination",
      command: "Logs and metrics export settings",
    },
    console: {
      label: "Console address",
      keywords: "console access publish expose console domain https certificate public name nginx tunnel allow list",
      command: "Console address settings",
    },
    tokens: { label: "API tokens", keywords: "automation ci scope", command: "API tokens" },
    about: { label: "About Noust", keywords: "about version update channel support report", command: "About Noust" },
    access: {
      label: "Roles",
      keywords: "roles and access permissions custom role grant binding team project environment scope least privilege",
      command: "Roles settings",
    },
    compliance: {
      label: "ENS report",
      keywords: "compliance ens esquema nacional seguridad check report audit evidence",
      command: "ENS report",
    },
    cloud: {
      label: "Cloud providers",
      keywords: "cloud hetzner digitalocean vultr provider token cloud-init create server vps droplet",
      command: "Cloud providers settings",
    },
    central: { label: "Central", keywords: "seal passphrase unlock hub role fleet", command: "This central's settings" },
  },
  /** Screens with no name of their own anywhere else: the account's and the controls', and the recipes. */
  screens: {
    account: { label: "Profile and password", keywords: "me my user profile password name email", command: "Your account" },
    accountSecurity: {
      label: "Sign-in security",
      keywords: "two-factor 2fa authenticator totp passkeys sessions sign in security",
      command: "Sign-in security",
    },
    controls: { label: "Controls", keywords: "compliance frameworks ens security profile policy", command: "Controls settings" },
    recipes: { label: "Recipes", command: "Recipes" },
    installedRecipes: { label: "Installed recipes", keywords: "recipes installed update available", command: "Installed recipes" },
    teams: { keywords: "people members roles organise group workspace" },
    terminal: { keywords: "shell ssh console command line" },
    files: { keywords: "browse edit upload download folder" },
    diagnose: { keywords: "troubleshoot why down broken health check" },
  },
  /** What these screens were called before the 4.0 navigation: still search words in the palette (RENAMED). */
  renamed: {
    overview: "Overview",
    cron: "Cron",
    backups: "Backups",
    server: "Server",
    recipes: "Recipes",
    recipesInstalled: "Installed recipes",
    projects: "Projects",
    domains: "Domains and certificates",
    notifications: "Notifications",
    alerts: "Alerts",
    console: "Console access",
    about: "About",
    access: "Roles and access",
    compliance: "Compliance",
    probes: "Probes",
    logShipping: "Log shipping",
    cloud: "Cloud",
    security: "Security",
  },
} as const;
