/**
 * The page Settings opens on (features/settings/SettingsHome.tsx): every section as a door, in the
 * groups the second column draws, each with one line on what is inside. A newcomer finds a setting by
 * what it is for here, before they know its name (task 18 of the information architecture).
 */
export const settingsHome = {
  documentTitle: "Settings",
  lead: "Everything you can set up, by what it is for.",
  doors: "Settings in {group}",
  summary: {
    accounts: "Who has an account, and what each person can do.",
    access: "The roles that decide it, and what each allows.",
    sso: "Let people sign in with your company's directory.",
    approvals: "Who must approve the riskiest changes.",
    console: "The name the console answers on, over HTTPS.",
    notifications: "Where messages go when something happens.",
    "log-shipping": "Send logs and metrics to another service.",
    integrations: "GitHub, GitLab and where your code lives.",
    "deploy-keys": "An SSH key for each repository, and the host keys pinned.",
    registries: "Logins to pull private images.",
    "secret-managers": "Vaults your variables can read from.",
    cloud: "Cloud accounts that create servers for you.",
    compliance: "How you stand against a framework, such as ENS.",
    controls: "The rules each framework turns on, one by one.",
    audit: "Who did what, and when.",
    tokens: "Keys for scripts and CI.",
    api: "Every call the API accepts.",
    manifest: "This server written down as a file.",
    mcp: "Connect outside programs through MCP.",
    fleet: "What every server shares, and which one differs.",
    servers: "Add, link or remove servers.",
    central: "How this central is kept and sealed.",
    general: "Name, folders, web server and builds.",
    variables: "Variables every app on the server can use.",
    about: "Version, updates and what changed.",
  },
  facts: {
    people: { one: "{count} person", other: "{count} people" },
    roles: { one: "{count} role", other: "{count} roles" },
    version: "Version {version}",
  },
} as const;
