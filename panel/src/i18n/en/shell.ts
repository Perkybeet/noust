/**
 * Strings of the console's shell area: the frame around every page (Shell, Topbar, Sidebar,
 * the command palette, keyboard shortcuts, the machine strip, the mobile menu, the page-level
 * error boundary). Destinations themselves live in nav.ts.
 */
export const shell = {
  area: "Noust Console",
  /** The wordmark's product suffix (Logo's `product` prop), apart from "Noust Console" above. */
  consoleProduct: "Console",
  /** The sidebar's logo link, which doubles as the overview link. */
  sidebarOverview: "Noust Console, overview",
  version: "Version {version}",
  skipToContent: "Skip to content",
  menu: {
    title: "Menu",
    open: "Open menu",
  },
  session: {
    label: "Session and preferences",
    title: "Session",
    signedInTo: "Signed in to {hostname}",
    theme: "Theme",
    keyboardShortcuts: "Keyboard shortcuts",
    signOut: "Sign out",
  },
  search: {
    label: "Search",
    placeholder: "Search pages, applications and actions",
    dialogTitle: "Search the console",
    results: "Results",
    noMatches: 'Nothing matches "{query}".',
    moveHint: "to move",
    openHint: "to open",
    closeHint: "to close",
  },
  commandGroups: {
    pages: "Pages",
    applications: "Applications",
    actions: "Actions",
  },
  commands: {
    newApplication: "New application",
    newApplicationKeywords: "deploy create",
    followSystemTheme: "Follow the system theme",
    switchToLightTheme: "Switch to the light theme",
    switchToDarkTheme: "Switch to the dark theme",
    themeKeywords: "theme appearance colour color mode",
    shortcutsKeywords: "help keys",
    signOutKeywords: "log out logout exit",
  },
  shortcuts: {
    goToDeployments: "Go to deployments, or activity outside an app",
    searchPage: "Search this page, or everything",
    showShortcuts: "Show keyboard shortcuts",
    openPalette: "Open the command palette",
    dialogDescription: "Shortcuts work anywhere except while you type in a field.",
    then: "then",
  },
  theme: {
    system: "System",
    light: "Light",
    dark: "Dark",
  },
  machine: {
    landmark: "This machine",
    unitsSummary: "Noust units: {running} running, {failed} failed, {stopped} stopped",
    load: "Load",
    loadAverage: "Load average: {one} {five} {fifteen}",
    loadDetail: "over one minute, {five} over five, {fifteen} over fifteen",
    units: "Units",
    memory: "Memory",
    disk: "Disk",
    upFor: "up {duration}",
    unavailable: "Machine readings unavailable",
    reconnecting: "Reconnecting",
    failedUnits: { one: "{count} failed unit", other: "{count} failed units" },
  },
  pageHeader: {
    breadcrumb: "Breadcrumb",
  },
  pageError: {
    title: "This page could not be displayed",
    defaultHint: "Reload to try again. If it fails the same way, the message below is what to report.",
    label: "The error",
    tryAgain: "Try again",
    reload: "Reload page",
  },
  notFound: {
    title: "Page not found",
    body: "Nothing lives at this address. Check the link, or go back to the {overview}.",
    overviewLink: "overview",
  },
  renameNotice: {
    text: "WASM is now Noust — nothing else changed: same console, same applications.",
    link: "What changed and why",
    dismiss: "Dismiss",
  },
} as const;
