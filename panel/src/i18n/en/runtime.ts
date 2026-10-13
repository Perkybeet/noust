/**
 * The words of how an application runs and is operated (3.4): reading a deploy's log by level,
 * how going back is done, the check a release must pass, what an application is set to that
 * it does not run with yet, its sandbox, its copies and processes, its mounts and files, and
 * where its logs are shipped.
 */
export const runtime = {
  deployLog: {
    levelLabel: "Log level",
    levelAll: "All",
    levelWarn: "Warnings and errors",
    levelError: "Errors",
    timesLabel: "Show times",
    share: "Copy to share",
    shareDescription: "Copies what is shown without colour codes and with anything that looks like a credential masked: safe to paste into a ticket.",
    shareCopied: "Copied the log, ready to share",
    shareFailed: "The log could not be prepared to share",
    nothingAtLevel: "No line of this log reads as a warning or an error.",
    filteredNote: "Showing {shown} of {total} lines.",
  },
  rollback: {
    instantWords: "Goes back in seconds: what deployment {id} ran is still on the server, so nothing is built.",
    rebuildWords: "Builds commit {commit} again where the application runs, which takes as long as a deploy.",
    instantBadge: "Instant",
    rebuildBadge: "Rebuilds",
  },
} as const;
