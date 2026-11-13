/** The console's destinations (app/nav.ts): names, palette search words, shortcut labels. */
export const nav = {
  landmarks: {
    main: "Main",
    appSections: "Application sections",
    settingsSections: "Settings sections",
    teamSections: "Team sections",
    accountSections: "Account sections",
    serverSections: "Server sections",
    databaseSections: "Database sections",
    serviceSections: "Service sections",
    /** The sections of an area that has no landmark of its own: "Network sections". */
    areaSections: "{area} sections",
  },
  /** The titles of the groups of the main bar: small, in sentence case, beside a joint. Home and Settings are not in a group. */
  groups: {
    runs: "Applications and data",
    reach: "Network",
    happens: "Observe",
    safe: "Protect",
    where: "Infrastructure",
  },
  /** What an application's sections are for: the titles of the groups of its second column. */
  intents: { use: "Use", configure: "Configure", fix: "Fix", move: "Move elsewhere", danger: "Danger" },
  /** What a database's sections are for: the titles of the groups of its second column (Danger is the application's word too). */
  dbIntents: { use: "View", configure: "Manage" },
  /** The titles of the groups of the column of an area whose sections are of different kinds. */
  columnGroups: {
    projects: "Projects",
    publish: "Publish",
    machine: "This machine",
    running: "What runs",
    docker: "Docker",
    tools: "Tools",
    fleet: "Fleet",
    dns: "DNS",
  },
  /** The fixed views of an area that is one page: what its column lists (features/nav/views.ts). */
  views: {
    appsAll: "All",
    appsProjects: "By project",
    appsStates: "By state",
    appsDeployments: "Deployments of all",
    logsApps: "Applications",
    logsSystem: "System",
    activityChanges: "Changes",
    activityDeployments: "Deployments",
    activityAccess: "Sign-ins",
  },
  /** The column itself, and the sheet it becomes on a phone. */
  column: {
    switch: "Switch",
    switchApp: "Switch application",
    switchDatabase: "Switch database",
    switchService: "Switch service",
    switchServer: "Switch server",
    sections: "Sections",
    sectionsOf: "Sections of {area}",
  },
  /** The main bar's own controls. */
  rail: {
    /** The name of the landmark of the main bar. */
    landmark: "Navigation",
    fold: "Fold the bar",
    unfold: "Unfold the bar",
  },
  /** The bar at the foot of a phone: four areas and the rest. */
  bottomBar: {
    label: "Areas",
    more: "More",
  },
  /** The sheet "More" opens on a phone: every area in its group, then Settings and the account. */
  sheet: {
    title: "All areas",
    account: "Your account",
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
    /** On the pages of accounts and tokens, which are the central's: the servers' own are managed on each server. */
    ownAccountsTitle: "Each server's accounts are managed on that server",
    ownTokensTitle: "Each server's tokens are managed on that server",
    ownNote: "A server does not let the central see or change its own: a central that was taken over could otherwise lock you out of every server. Sign in to its console or run this on it:",
    ownCommands: "On the server",
    titles: {
      people: "People and access",
      notices: "Notices",
      connections: "Connections",
      security: "Compliance and audit",
      api: "API",
      noust: "Noust",
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
  /** Activity's list of tasks, the history of the task centre (features/tasks). */
  activityTasks: { label: "Tasks", keywords: "jobs background running queue progress deploys backups operations", command: "Tasks in activity" },
  alerts: { label: "Alerts", goTo: "Go to alerts" },
  logs: { goTo: "Go to logs" },
  /** The area is "Servers" over every server (and the central's list) and "Server" on one. */
  server: { label: "Servers", labelOne: "Server", allServers: "All servers", keywords: "server machine health cpu memory disk processes monitor", goTo: "Go to servers" },
  network: { label: "Network", keywords: "ports firewall tunnels interfaces listen publish open close cloudflare nginx stream", goTo: "Go to network" },
  security: { label: "Security", keywords: "ssh keys passwords firewall fail2ban hardening checks brute force bans", goTo: "Go to security" },
  settings: { label: "Settings", keywords: "preferences configuration", goTo: "Go to settings" },
  /** The fleet's views, each the face of an area of the bar (features/nav/fleet.ts). */
  fleetBar: {
    overview: { label: "Overview" },
    servers: { label: "Servers" },
    network: { label: "Network" },
    security: { label: "Security" },
    apps: { label: "Applications" },
    certificates: { label: "Certificates" },
    backups: { label: "Backups" },
    updates: { label: "With an update", goTo: "Go to the servers with an update" },
    activity: { label: "Activity" },
    alerts: { label: "Alerts" },
  },
  appTabs: {
    overview: { label: "Overview" },
    deployments: { label: "Deployments", keywords: "deploys builds history rollback" },
    logs: { label: "Logs", keywords: "journal output" },
    metrics: { label: "Metrics", keywords: "cpu memory charts" },
    environment: { label: "Variables", keywords: "env environment secrets dotenv" },
    domains: { label: "Domains", keywords: "certificate ssl www names alias redirect" },
    code: {
      label: "Code and build",
      keywords: "settings source repository git branch tag build install start command container image compose runtime sandbox cache packages deploy key credential",
    },
    triggers: {
      label: "Deploys and triggers",
      keywords: "settings releases rollback instant startup check zero downtime automation schedule freeze deploy on push webhook hooks previews pull request",
    },
    resources: {
      label: "Resources and running",
      keywords: "settings limits memory cpu processes workers mounts folders persistent disk quota service security hardening",
    },
    access: {
      label: "Access and protection",
      keywords: "settings password basic auth addresses allowlist routes client certificates rate limit headers http3 waf",
    },
    export: { label: "Export and declaration", keywords: "settings backup move another server declaration yaml noust.yaml apply import" },
    servers: { label: "Several servers", keywords: "replicate replication spread fleet placement promote environments high availability" },
    delete: { keywords: "settings remove uninstall forget stop managing" },
  },
  teamTabs: {
    overview: { label: "Overview" },
    members: { label: "Members", keywords: "people roles invite owner access" },
    notifications: { label: "Notifications", keywords: "alerts slack ntfy telegram webhook channels notices" },
    settings: { label: "Settings", keywords: "rename delete name" },
  },
  settingsTabs: {
    overview: { label: "All settings", command: "Settings" },
    general: { label: "General", keywords: "server name address folder applications web server nginx apache builds freeze", command: "General settings" },
    notifications: {
      label: "Notification channels",
      keywords: "notifications alerts email slack webhook channels where messages go mute silence pause quiet",
      command: "Notification channels settings",
    },
    alerts: {
      label: "Alerts",
      keywords: "alerts alert rules threshold cpu memory disk firing monitor when to warn",
      command: "Alert rules",
    },
    checks: {
      label: "Checks",
      keywords: "checks built-in thresholds disk full certificate expiry missing backup api token system updates monitor limits",
      command: "Checks the monitor makes",
    },
    statusPage: {
      label: "Status page",
      keywords: "status page public uptime components publish incident",
      command: "Status page",
    },
    metrics: {
      label: "Metrics",
      keywords: "metrics cpu memory disk network load swap usage consumption charts history compare servers",
      command: "Metrics of the servers",
    },
    integrations: {
      label: "Integrations",
      keywords: "integrations connections github gitlab gitea bitbucket app repositories push pull request previews webhooks code host leases",
      command: "Integrations settings",
    },
    probes: {
      label: "Probes",
      keywords: "probes uptime monitor status page external check address http outage ping website down",
      command: "Probes",
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
      label: "Compliance",
      keywords: "compliance frameworks controls ens iso 27001 nis2 esquema nacional seguridad check report audit evidence normativas cumplimiento",
      command: "Compliance, frameworks and their report",
    },
    accessSecurity: {
      label: "Access security",
      keywords: "access security second factor two-factor sessions idle timeout lockout password length master token break-glass sign in",
      command: "How the console is signed in to",
    },
    cloud: {
      label: "Cloud providers",
      keywords: "cloud hetzner digitalocean vultr provider token cloud-init create server vps droplet",
      command: "Cloud providers settings",
    },
    central: { label: "Central and sealing", keywords: "central seal passphrase unlock hub role fleet", command: "This central's settings" },
  },
  /** Screens with no name of their own anywhere else: the account's and the controls', and the recipes. */
  screens: {
    account: { label: "Profile and password", keywords: "me my user profile password name email", command: "Your account" },
    accountSecurity: {
      label: "Sign-in security",
      keywords: "two-factor 2fa authenticator totp passkeys sessions sign in security",
      command: "Sign-in security",
    },
    controls: { label: "Controls" },
    recipes: { label: "Recipes", command: "Recipes" },
    installedRecipes: { label: "Installed recipes", keywords: "recipes installed update available", command: "Installed recipes" },
    teams: { keywords: "people members roles organise group workspace" },
    terminal: { keywords: "shell ssh console command line" },
    files: { keywords: "browse edit upload download folder" },
    diagnose: { keywords: "troubleshoot why down broken health check" },
    firewall: { keywords: "ufw firewalld nftables iptables rules allow deny open close port" },
    interfaces: { keywords: "network cards addresses ip ethernet eth0 mac mtu" },
    ssh: { keywords: "sshd keys authorized password login root port fail2ban" },
    blocks: { keywords: "fail2ban banned blocked ips jail brute force unban" },
    waf: { keywords: "modsecurity owasp crs web application firewall attacks injection rules" },
    access: { keywords: "who logged in sessions ssh logins terminal recordings audit entered" },
    processes: { keywords: "top ps cpu memory heavy running programs kill" },
    monitor: { keywords: "resources cpu memory disk network load charts usage history" },
    updates: { keywords: "update upgrade packages apt dnf security patches reboot noust version" },
  },
  /** What these screens were called before the 4.0 navigation: still search words in the palette (RENAMED). */
  renamed: {
    overview: "Overview",
    cron: "Cron",
    engines: "Engines",
    backups: "Backups",
    server: "Server",
    systemUpdates: "System updates",
    thisServer: "This server",
    bruteForce: "Brute force",
    fleetUpdates: "Fleet updates",
    recipes: "Recipes",
    recipesInstalled: "Installed recipes",
    projects: "Projects",
    domains: "Domains and certificates",
    webServerSites: "Web server sites",
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
    integrations: "Connections",
    accounts: "Accounts",
    variables: "Variables",
    general: "Settings",
    serverSecurity: "Security and SSH",
    serverNetwork: "Network and ports",
    domainsPorts: "Domains, published ports",
    domainsTunnels: "Domains, tunnels",
    settingsServers: "Settings, Servers",
    channels: "Channels",
    fleetSettings: "Fleet settings",
    settingsManifest: "Settings, Manifest",
    frameworks: "Frameworks",
    controls: "Controls",
    people: "People",
  },
} as const;
