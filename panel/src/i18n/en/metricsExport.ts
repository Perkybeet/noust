/**
 * Strings of the section of Settings > Integrations that serves the metrics to a scraper. The
 * address, the configuration and the commands are shown as they are; only the words around them are
 * here.
 */
export const metricsExport = {
  title: "Metrics for a scraper",
  description: "Prometheus, Grafana Alloy, Netdata and an OpenTelemetry Collector read the newest value of every metric of this server, of each application and each container, from one address.",
  addressLabel: "Address to scrape",
  copyAddress: "Copy the address to scrape",
  configLabel: "A Prometheus entry for it",
  copyConfig: "Copy the Prometheus entry",
  credential: "The scraper needs a token that can only read, in the file named above. Make one in {tokens}; nothing else is needed, and it can change nothing.",
  tokensLink: "API tokens",
  createToken: "Create the token from a terminal",
  fromTerminal: "The same text, from a terminal",
} as const;
