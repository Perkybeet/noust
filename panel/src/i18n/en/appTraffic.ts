/**
 * Strings of the traffic section of an application's Metrics tab (what its access log says) and of
 * the per-container charts of a Compose stack. A page, a network, a status code and a referrer's
 * host are shown as they were counted; only the words around them are here.
 */
export const appTraffic = {
  traffic: {
    title: "Who asks for what",
    description: "From the web server's access log, by the hour. No query string is kept, and a visitor is their network, not their address.",
    windowLabel: "Traffic window",
    window: { "24h": "24 hours", "7d": "7 days", "30d": "30 days", "90d": "90 days" },
    loading: "Reading the traffic",
    loadFailed: "Could not read the traffic",
    requests: "Requests",
    requestsDetail: "{ok} answered well",
    errors: "Server errors",
    errorsDetail: "{share} of the requests",
    clientErrors: "Client errors (4xx)",
    served: "Data served",
    visitors: "Visitors",
    visitorsDetail: "Different addresses with their browsers",
    latency: "Response time (p95)",
    latencyDetail: "Half took under {median}",
    chartTitle: "Requests over time",
    hourly: "1-hour totals",
    daily: "1-day totals",
    hourCell: "1-hour total",
    dayCell: "1-day total",
    noRequests: "No request in this window.",
    rankings: {
      paths: "Most asked-for pages",
      networks: "Networks",
      statuses: "Status codes",
      referrers: "Where they came from",
    },
    rankingEmpty: "Nothing yet.",
  },
  containers: {
    title: "By container",
    description: "Each container of the stack on its own, so the service that reads the disk is the one named.",
    roles: {
      cpu: "CPU by container",
      memory: "Memory by container",
      disk_read: "Disk read by container",
      disk_write: "Disk write by container",
      net_rx: "Received by container",
      net_tx: "Sent by container",
    },
    busiest: "Showing the {shown} busiest of {total} containers.",
  },
} as const;
