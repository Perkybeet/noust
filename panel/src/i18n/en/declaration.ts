/**
 * An application as a declaration file, and an application set from one (an application's
 * Export settings). What the server refuses is shown verbatim and is not here.
 */
export const declaration = {
  title: "Declaration file",
  description: "How this application is run (health check, limits, source options, build commands, hooks, non-secret variables, domains and tasks) in the keys of noust.yaml. Written from this application, or applied to it.",
  download: "Download the declaration",
  downloaded: "The declaration of {domain} was downloaded",
  downloadFailed: "Could not write the declaration",
  secrets: "Secret variables are never in it: their names are listed in a comment.",
  apply: "Apply a declaration…",
  dialog: {
    title: "Apply a declaration to {domain}",
    description: "Paste a declaration file. Only the sections it has are changed, and nothing is changed until you apply it.",
    label: "Declaration (YAML)",
    placeholder: "app:\n  health:\n    path: /up\nenv:\n  LOG_LEVEL: info",
    check: "Check what it changes",
    failed: "The declaration could not be checked or applied",
    prune: "Also remove the aliases, redirects and tasks it does not list",
    pruneDescription: "Without this, domains and tasks are only added. Variables are never removed.",
    close: "Close",
    applyNow: "Apply it",
    applied: "The declaration was applied to {domain}",
    partly: "{count} part(s) were not applied",
  },
  plan: {
    title: "What it changes",
    nothing: "Nothing: the application already is as declared.",
    already: "Already as declared: {sections}",
    runsCommands: "Runs commands",
    confirm: "I have read what it runs as the application or on this machine, and I agree",
    notApplied: "Not applied",
  },
  fromTerminal: "The same, from a terminal",
} as const;
