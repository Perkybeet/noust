/**
 * A monorepo deployed as one application per workspace (the new-app wizard's source step). What
 * the repository or the server refuses is shown verbatim and is not here.
 */
export const workspaces = {
  open: "Deploy the workspaces of a monorepo",
  openHint: "A repository with several applications in it becomes one application per workspace.",
  title: "Deploy the workspaces of a monorepo",
  description: "Noust reads which workspaces are applications and creates one application for each you choose, with its own domain. A push deploys an application only when it touches that workspace or something it depends on.",
  source: "Repository",
  sourceDescription: "A Git URL, an archive URL or a path on this server.",
  branch: "Branch",
  read: "Read the workspaces",
  readFailed: "The workspaces could not be read",
  found: "{count} workspaces, managed with {tool} and {manager}.",
  foundNone: "This repository declares no workspaces.",
  problems: "What was found wrong",
  applications: "Applications",
  libraries: "Not applications",
  dependsOn: "Depends on",
  watches: "Deploys when it changes",
  domainFor: "Domain for {name}",
  choose: "Deploy {name}",
  none: "None of the workspaces is an application.",
  deploy: "Deploy {count} applications",
  deployOne: "Deploy 1 application",
  deployFailed: "The applications could not be created",
  queuedTitle: "The applications are being created",
  queuedBody: "Each one builds on its own. Follow it from its page.",
  open_app: "Open {domain}",
} as const;
