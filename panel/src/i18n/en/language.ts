/** Choosing the console's language (Settings > General, the session menu). */
export const language = {
  label: "Language",
  description:
    "The language of this console in this browser. It is saved here, not in the configuration file. What Noust, nginx and systemd print is shown as they write it.",
  loadFailed: "Could not switch the language",
  loadFailedHint: "The translation did not download. Check the connection to the console and try again.",
} as const;
