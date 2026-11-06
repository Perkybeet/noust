/**
 * Strings of Settings > Access security (features/settings/accessSecurity): how people get into the console. Each
 * setting on the page is a control of the compliance frameworks, whose own words are `complianceSetup`'s.
 */
export const accessSecurity = {
  loadFailed: "Could not read the access settings",
  loading: "Loading the access settings",
  door: {
    title: "At the door",
    description: "What is asked of whoever signs in, and what stops a stranger who guesses.",
    addresses: "Only from these addresses",
    addressesHelp: "Set in the configuration (web.ip_whitelist), not here.",
    anyAddress: "Any address",
  },
  sessions: {
    title: "Sessions",
    description: "How long a person stays signed in without doing anything, and for how long at most.",
    yours: "Your sessions",
    open: "Open now",
    openCount: { one: "{count} session", other: "{count} sessions" },
  },
  master: {
    title: "Master access token",
    description: "For when nothing else works: it signs in without an account. Keep it outside this server.",
    help: "The token the installer printed signs in with no account and no second factor of a person. Break-glass keeps it as a way in; account recovery only keeps it for recovering an account, and nothing else.",
  },
} as const;
