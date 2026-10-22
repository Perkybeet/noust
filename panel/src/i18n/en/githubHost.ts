/**
 * Which GitHub the integration talks to (Settings, Integrations): github.com, or a GitHub
 * Enterprise Server or Cloud host. What the server refuses is shown verbatim and is not here.
 */
export const githubHost = {
  title: "Which GitHub",
  description: "github.com, or the address of your GitHub Enterprise Server or Cloud. It decides where the App is created and what Noust asks for tokens, statuses and clones.",
  label: "GitHub address",
  fieldDescription: "Leave it empty for github.com. For Enterprise, the host without https://, such as ghe.example.com.",
  placeholder: "github.com",
  save: "Use this GitHub",
  saved: "The integration now talks to {host}",
  failed: "The address could not be saved",
  fact: "GitHub",
  factDescription: "The host the App lives on. It cannot change while the App exists.",
} as const;
