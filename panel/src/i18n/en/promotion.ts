/**
 * Strings of what an application can be turned into without building it by hand: promoting a
 * release to the same app in another environment, and cloning an app. Both are dialogs opened
 * from an app's menu and from its header.
 */
export const promotion = {
  promote: {
    menu: "Promote to another environment",
    menuDescription: "Put the release that serves here on this app's twin in another environment, without building it again",
    title: "Promote {domain}",
    description:
      "The release that serves {domain} is copied to the same app in another environment of its project and activated there, without being built again. It must pass the startup check there, and what served there comes back if it does not.",
    loading: "Loading where {domain} can be promoted",
    loadFailed: "Could not read where {domain} can be promoted",
    release: "Release",
    commit: "Commit",
    noRelease: "{domain} has no release to promote",
    noReleaseHint: "Only an app with instant rollback has releases. Turn it on in the app's deploy settings, deploy once, then promote.",
    noTargetsTitle: "No other environment has this app",
    noTargetsHint:
      "A release is promoted to the same app (same repository and type) in another environment of this project. Clone this app into that environment first, then promote.",
    targetLabel: "Promote to",
    targetDescription: "Applications of this project in other environments, built from the same repository.",
    targetRuns: "runs {release}",
    targetEmpty: "no release yet",
    moreOptions: "More options",
    schemaLabel: "Allow going back past a schema change",
    schemaDescription: "Only needed when the target already ran a later release that changed its database's schema. Noust puts code back, never a database.",
    submit: "Promote release",
    queued: "Promoting release {release} to {target}",
    failed: "The release was not promoted",
  },
  clone: {
    menu: "Clone application",
    menuDescription: "Make another app with these settings, in this or another environment",
    title: "Clone {domain}",
    description:
      "A new app with this one's settings, variables, hooks, tags, cron jobs and previews, deployed from the same source and built, checked and recorded like any other. Its data is not copied, and neither are the names this app also answers on.",
    domainLabel: "Domain of the clone",
    domainDescription: "Where the clone answers, such as staging.example.com.",
    domainRequired: "Give the domain the clone answers on.",
    dataLegend: "Its database",
    dataEmpty: "A database of its own",
    dataEmptyDescription: "An empty database made for the clone, which gets none of this app's connection settings.",
    dataShare: "This app's database",
    dataShareDescription: "The clone connects to the same database, so what either writes, the other reads.",
    dataNeeded: "Choose what the clone does with the database.",
    moreOptions: "More options",
    sourceLabel: "Deploy from another source",
    sourceDescription: "A repository or a directory the clone is built from instead of this app's.",
    submit: "Clone application",
    queued: "Cloning {domain} as {clone}",
    failed: "{domain} was not cloned",
  },
} as const;
