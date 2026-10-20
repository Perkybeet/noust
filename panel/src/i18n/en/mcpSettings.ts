/**
 * Strings of Settings > MCP: the endpoint that lets an MCP client (an editor, a command-line tool,
 * a script) inspect, deploy and control applications with an API token, off until an operator turns
 * it on; which groups of tools it offers; and what each tool is. Tool names and what a tool says
 * about itself come from the server as it wrote them.
 */
export const mcpSettings = {
  nav: {
    label: "MCP",
    keywords: "mcp model context protocol endpoint tools client token inspect deploy control llms",
    command: "MCP settings",
  },
  documentTitle: "MCP",
  loadFailed: "Could not read the MCP settings",
  loadingLabel: "the MCP settings",
  state: {
    title: "MCP endpoint",
    description: "A client can do exactly what its token allows, and every call is in the audit log.",
    label: "Answer MCP clients",
    help: "Lets a client with an API token inspect, deploy and control applications.",
    on: "On",
    off: "Off",
    turnedOn: "The MCP endpoint answers now",
    turnedOff: "The MCP endpoint is off",
    failed: "Could not change the MCP endpoint",
  },
  connection: {
    title: "Connecting a client",
    description: "Give the client this address and a token in the Authorization header. A browser session and the access token are not accepted: only an API token.",
    address: "Endpoint address",
    copyAddress: "Copy the endpoint address",
    token: "A token with only what it needs",
    tokenHelp: "Make one that holds just the permissions of the tools you want, optionally bound to a team, a project or an environment.",
    tokensLink: "Open API tokens",
    protocols: "Protocol versions",
  },
  groups: {
    title: "What it offers",
    description: "Tools come in three groups. Turn a group off and its tools are not listed or callable, whatever the token holds.",
    toolCount: { one: "{count} tool", other: "{count} tools" },
    inspect: { name: "Inspect", description: "Read-only: applications, logs, deployments, health. It never changes anything." },
    deploy: { name: "Deploy", description: "Update, roll back and back up an application, and cancel a job." },
    control: { name: "Control", description: "Restart, start and stop an application, and renew a certificate." },
    changed: "The groups offered were changed",
  },
  tools: {
    title: "Tools",
    description: "What a client sees, with the permission each one needs. Values of variables and secrets are never returned by any of them.",
    tableCaption: "MCP tools",
    column: { tool: "Tool", group: "Group", permission: "Permission", effect: "Effect" },
    reads: "Reads",
    changes: "Changes",
    asksTwice: "Asks twice",
    prompts: "Prompts",
    resources: "Resources",
  },
  command: "From a terminal",
  commandEnable: "noust mcp enable",
  commandToken: "noust mcp token ci-bot --preset deploy --owner maria",
} as const;
