/**
 * Strings of the console's server plumbing: the server selector in the top bar and the
 * palette (`selector`), the notices the shell shows while on a node (`shell`), a page a node
 * does not offer (`capability`) and a node the central could not use (`errors`). Node names
 * and versions are data, never translated.
 */
export const fleet = {
  selector: {
    label: "Server",
    trigger: "Server: {name}",
    triggerWithStatus: "Server: {name}, {status}",
    thisServer: "This server",
    version: "Noust {version}",
    noVersion: "Version not known yet",
    status: {
      reachable: "Reachable",
      unreachable: "Unreachable",
      refused: "Refused",
      unknown: "Not checked yet",
    },
    listFailed: "Could not list the servers",
    switched: "Now on {name}",
    switchTo: "Switch to server {name}",
    switchToThisServer: "Switch to this server, {name}",
    keywords: "server node fleet machine switch",
  },
  shell: {
    noticeLabel: "About {node}",
    olderNode:
      "{node} runs Noust {nodeVersion}, older than this server's {version}. A page that needs something {node} does not have says so.",
    newerNode:
      "{node} runs Noust {nodeVersion}, newer than this server's {version}. This console shows only what it knows; update this server to use everything {node} offers.",
    unknownNode: "This server has no node named {node}.",
    backToThisServer: "Go to this server",
  },
  capability: {
    notAvailable: "Not available on {node} (Noust {version})",
    notAvailableNoVersion: "Not available on {node}",
    explanation: "The Noust on {node} does not offer this. Update Noust on {node} to use it here.",
    checking: "Checking what {node} offers",
  },
  errors: {
    unreachableTitle: "{node} is not answering",
    refusedTitle: "{node} refused this server",
    unreachableHint: "This server could not reach {node} through its tunnel.",
    refusedHint: "{node} no longer accepts this server's fleet token.",
  },
} as const;
