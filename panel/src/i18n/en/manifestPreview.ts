/**
 * Strings of Settings > Manifest: pasting a declarative manifest of the applications of a server
 * and seeing what applying it would change here, before anything does. Applying is the command
 * line's, from the repository the manifest lives in. What the server refuses a document with is
 * shown as it wrote it.
 */
export const manifestPreview = {
  nav: {
    label: "Manifest",
    keywords: "manifest declarative yaml plan apply infrastructure as code gitops preview diff",
    command: "Manifest preview",
  },
  documentTitle: "Manifest",
  intro: {
    title: "Preview a manifest",
    description:
      "Paste the manifest of this server's applications to see what applying it would change here. A preview changes nothing, and nothing in it is ever deleted when it is applied.",
  },
  form: {
    label: "Manifest",
    help: "YAML or JSON. A variable read from the environment or from a file is resolved by the machine that applies the manifest, never by this server.",
    placeholder: "version: 1\napplications:\n  - domain: shop.example.com\n    source: https://github.com/acme/shop\n    branch: main",
    preview: "Preview changes",
    errorTitle: "The manifest was not accepted",
  },
  result: {
    title: "What applying it would change",
    nothing: "Nothing to change",
    nothingBody: "This server already matches the manifest.",
    summary: { one: "{count} change, in this order", other: "{count} changes, in this order" },
    tableCaption: "Changes a manifest would make",
    column: { change: "Change", target: "Target", what: "What happens" },
    needsSudo: "Needs sudo mode",
    unchecked: "Not checked here",
    warningsTitle: "Differences that are not reconciled",
    unmanagedTitle: "Applications the manifest does not name",
    unmanagedBody: "Applying never deletes them: {names}",
    applyHint: "Apply it from the repository the manifest lives in, with a token that can be revoked.",
  },
  kind: {
    projectCreate: "Create project",
    environmentCreate: "Create environment",
    appCreate: "Create application",
    appMove: "Move application",
    appBranch: "Change branch",
    appLimits: "Change limits",
    appHealth: "Change health check",
    appDomain: "Add domain",
    appDatabase: "Add database",
    appEnv: "Set variables",
    appBackups: "Schedule backups",
  },
  command: "From a terminal",
  commandPlan: "noust --context prod manifest plan noust-manifest.yaml",
  commandExport: "noust --context prod manifest export > noust-manifest.yaml",
} as const;
