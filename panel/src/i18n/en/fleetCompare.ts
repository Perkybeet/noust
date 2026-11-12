/**
 * Strings of what a central puts together from its servers: one metric of every server side by
 * side, and one search over the logs of every server. A log line and a server's own words are shown
 * as they came; only the words around them are here.
 *
 * Also the words of the pieces that let any page speak of several servers at once (`ScopeLine`,
 * `FleetList`, `ServersTable`, `WhichServerDialog`: `names`, `scope`, `list`, `stale`, `which`, `table`).
 * They live here and not in a namespace of their own so that the loader of the first load does not
 * grow by one namespace. Server names and versions are data.
 */
export const fleetCompare = {
  metrics: {
    title: "Compare servers",
    description: "Memory, swap and disk are the share each server has.",
    metricLabel: "What to compare",
    windowLabel: "How far back",
    metric: {
      cpu: "CPU",
      memory: "Memory",
      swap: "Swap",
      disk: "Disk",
      load: "Load (1 minute)",
      net_rx: "Network received",
      net_tx: "Network sent",
    },
    window: { "1h": "1 hour", "6h": "6 hours", "24h": "24 hours", "7d": "7 days", "30d": "30 days" },
    caption: "{metric} of every server, last {window}",
    latest: "Now",
    average: "Average",
    peak: "Peak",
    noReading: "This server recorded nothing in the window",
    gaps: { one: "{count} server did not answer, so it is missing here.", other: "{count} servers did not answer, so they are missing here." },
    none: "No server recorded this in the window.",
    loadFailed: "Could not compare the servers",
  },
  logs: {
    scopeLabel: "Which servers",
    thisServer: "This server",
    label: "the logs of every server",
    summary: { one: "{count} matching line from {servers} servers.", other: "{count} matching lines from {servers} servers." },
    nothing: "No server has a line that matches.",
    perServer: "Up to {limit} lines from each server.",
  },
  names: {
    central: "central",
    server: "Server",
  },
  scope: {
    all: {
      one: "From the one server that has it.",
      other: "From the {count} servers that have it.",
    },
    none: "No server has it.",
    server: "From {server}.",
    serverHint: "To see them all, choose All servers above.",
    library: "The same on every server: the server is chosen when you use it.",
    troubleLabel: "Servers that did not answer in full",
    stale: "{server} has not answered for {age}: what it last gave is shown.",
    staleNoAge: "{server} has not answered: what it last gave is shown.",
    unreachable: "{server} does not answer: nothing of it is shown.",
    unsupported: "{server} runs Noust {version}, which does not know this view: it is not listed here.",
    unsupportedNoVersion: "{server} runs a Noust that does not know this view: it is not listed here.",
  },
  list: {
    everyServer: "All servers ({count})",
    serverOption: "{server} ({count})",
    nothingFrom: "Nothing from {server} here.",
    showEvery: "Show every server",
  },
  /** The mark of the rows of a server that did not answer now, in a list and in a table of servers. */
  stale: {
    dataFrom: "Data from {age} ago",
    lastGiven: "The last data it gave",
  },
  which: {
    title: "On which server?",
    staysAsIs: "The server chosen above stays as it is.",
    choose: "Choose a server.",
    noneFit: "No server can take this now: each one says why.",
    offline: "No connection for {age}",
    offlineNoAge: "No connection",
    unsupported: "Its Noust, {version}, does not support this",
    unsupportedNoVersion: "Its Noust does not support this",
  },
  table: {
    state: "State",
    openOn: "Open on {server}",
    noView: "Its Noust does not have this view",
    noAnswer: "Nothing to show: it does not answer.",
    notAvailable: "Not available",
    notAnswering: "Not answering",
  },
} as const;
