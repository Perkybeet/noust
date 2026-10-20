/**
 * The inbox: the bell in the top bar and what it opens (features/inbox). "Needs you" is a state,
 * not a history - a failure stays there while it is true, whether or not the console was open when
 * it happened - and "Recent" is only what the server said while this browser tab was open;
 * the history is Activity. The sentences about what is wrong are the Overview's
 * (`overview.attention.*`); what is here is the frame, and the kinds of item the console adds.
 */
export const inbox = {
  /** The server the inbox speaks for, on a fleet. */
  forServer: "Needs you on {name}",
  tabs: {
    label: "Inbox",
    needs: "Needs you {count}",
    needsNone: "Needs you",
    recent: "Recent",
  },
  needs: {
    checking: "Checking what needs you",
    emptyTitle: "Nothing needs you.",
    emptyDescription: "A failed application, a certificate about to expire, an alert that fires or a request waiting for your decision shows here until it is dealt with.",
    count: { one: "{count} item needs you", other: "{count} items need you" },
    couldNotLoad: "Could not read what needs you",
  },
  recent: {
    emptyTitle: "No notices yet.",
    emptyDescription: "What the server tells you while this tab is open - a deploy that finished, a backup that failed - is kept here.",
    footer: "Since you opened the console",
    busy: "In progress",
    active: "Done",
    failed: "Failed",
    idle: "Notice",
  },
  history: "Open Activity",
  reasons: {
    alertFiring: "This alert is firing.",
    alertTaken: "This alert is firing. {by} took it.",
    probeDown: "This probe is down.",
    approvalWaiting: "{by} is waiting for your decision.",
    noustUpdate: "Noust {version} is available. This server runs {current}.",
    noustSecurityUpdate: "Noust {version} fixes a security problem. This server runs {current}.",
    recipeUpdate: "{recipe} has a newer version, {version}. It runs {current}.",
  },
  actions: {
    acknowledge: "Acknowledge",
    acknowledgeAria: "Acknowledge {subject}",
    reopen: "Reopen",
    reopenAria: "Reopen {subject}",
    acknowledgeFailed: "Could not take the alert",
    reopenFailed: "Could not reopen the alert",
    openProbes: "Open probes",
    openProbesAria: "Open probes, {name}",
    review: "Review",
    reviewAria: "Review request {id}",
    openUpdates: "Open updates",
    openUpdatesAria: "Open the updates of Noust",
    reviewRecipe: "Review",
    reviewRecipeAria: "Review the update of {domain}",
  },
} as const;
