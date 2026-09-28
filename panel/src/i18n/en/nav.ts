/** The console's destinations (app/nav.ts): names, palette search words, shortcut labels. */
export const nav = {
  landmarks: {
    main: "Main",
    appSections: "Application sections",
    settingsSections: "Settings sections",
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
    diagnose: { label: "Diagnose", keywords: "down why broken health" },
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
  },
} as const;
