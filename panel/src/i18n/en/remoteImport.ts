/**
 * Reading an application from a Coolify or Dokploy server over its API, in the new-app wizard's
 * import step. What the other server or Noust refuses is shown verbatim and is not here.
 */
export const remoteImport = {
  disclosure: "Read it from Coolify or Dokploy",
  title: "From another platform",
  description: "Noust reads an application over the other server's API with a read-only token and proposes how it runs here. Nothing is written to the other server. The token is used for this request and is not stored.",
  platform: "Platform",
  platforms: {
    coolify: "Coolify",
    dokploy: "Dokploy",
  },
  url: "Address of the server",
  urlDescription: "Where its API answers, such as https://coolify.example.com.",
  token: "API token",
  tokenDescription: "A read-only token created on that server.",
  list: "List the applications",
  listFailed: "The applications could not be listed",
  applications: "Application",
  applicationsDescription: "{count} found on {url}.",
  none: "That server has no applications.",
  choose: "Choose an application",
  domain: "Domain to create it on here",
  domainDescription: "Leave it empty to use the first one it answered on there.",
  read: "Read it",
  readFailed: "The application could not be read",
  loadedTitle: "{name} was read",
  loadedBody: "Review it in the next step: nothing is created until you deploy.",
  notDeployable: "{name} cannot be imported as it is",
  notDeployableBody: "It ran an image, a Dockerfile or a Compose file, which an export does not carry. This command creates the equivalent here.",
  warnings: "What has no equivalent here",
  label: "{platform}: {name}",
  fromTerminal: "The same, from a terminal",
} as const;
