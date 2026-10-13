/**
 * Strings of the tags an app carries to be grouped: the row in its General settings, the filter
 * on the applications list and updating every app that carries one. Not the git tags an app can
 * deploy (`appSettings.tags`).
 */
export const appTags = {
  row: {
    label: "App tags",
    none: "None",
    hint: "Group apps to filter the list and update them together.",
    edit: "Edit tags",
  },
  dialog: {
    title: "Tags of {domain}",
    description: "Names that group apps, such as web or client-acme. An app carries up to {max}.",
    label: "Tags",
    fieldDescription: "Separated by commas or spaces. 1 to 32 letters, digits, dots, underscores or dashes, starting and ending with a letter or digit.",
    placeholder: "web, client-acme",
    submit: "Save tags",
    saved: "Tags of {domain} saved",
    tooMany: "At most {max} tags.",
    failed: "The tags of {domain} were not saved",
    invalid: {
      one: "Not a tag: {list}. A tag is 1 to 32 letters, digits, dots, underscores or dashes, starting and ending with a letter or digit.",
      other: "Not tags: {list}. A tag is 1 to 32 letters, digits, dots, underscores or dashes, starting and ending with a letter or digit.",
    },
  },
  filter: {
    label: "Tag",
    every: "Every tag",
  },
  update: {
    action: "Update tagged apps",
    title: "Update the apps tagged {tag}?",
    description: {
      one: "{count} app carries {tag}. It pulls its source and builds a new release, as Update does, and is skipped when there is nothing new.",
      other: "{count} apps carry {tag}. Each pulls its source and builds a new release, as Update does, and is skipped when there is nothing new.",
    },
    force: "Rebuild even when there is nothing new",
    confirm: "Update apps",
    queued: {
      one: "Updating {count} app tagged {tag}",
      other: "Updating {count} apps tagged {tag}",
    },
    skipped: {
      one: "{count} app had nothing new and was skipped",
      other: "{count} apps had nothing new and were skipped",
    },
    failedTitle: {
      one: "{count} app could not be updated",
      other: "{count} apps could not be updated",
    },
    nothing: "No app tagged {tag} was updated",
    failed: "The apps tagged {tag} were not updated",
  },
} as const;
