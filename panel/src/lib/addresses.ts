/**
 * Addresses and networks as a form takes them: an allow-list typed as text, read into the
 * entries the server will check again (a database's public access, the console's published
 * name). The server is the judge of what an address really is; this keeps a typo from costing
 * a round trip.
 */

const IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\/(?:3[0-2]|[12]?\d))?$/;
const IPV6 = /^(?=.*:)[0-9a-fA-F:.]+(?:\/(?:12[0-8]|1[01]\d|\d?\d))?$/;

/** An address or a network, IPv4 or IPv6, as the form accepts it. */
export function isAddressOrNetwork(text: string): boolean {
  return IPV4.test(text) || IPV6.test(text);
}

export interface ParsedAddresses {
  entries: string[];
  /** The entries that are neither an address nor a network, in the order typed. */
  invalid: string[];
}

/** Reads "203.0.113.7, 10.0.0.0/24" (commas, semicolons, spaces or lines), once each. */
export function parseAddresses(text: string): ParsedAddresses {
  const found = [...new Set(text.split(/[\s,;]+/).filter((entry) => entry !== ""))];
  return { entries: found.filter(isAddressOrNetwork), invalid: found.filter((entry) => !isAddressOrNetwork(entry)) };
}
