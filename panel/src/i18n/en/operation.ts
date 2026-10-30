/**
 * Strings shared by the areas of what happens and what is kept safe (Logs, Alerts, Activity and
 * Backups) when they read one resource across the servers: the servers that did not let the operator
 * read it, the filter that narrows it to some servers and the person who did something.
 */
export const operation = {
  refused: {
    title: {
      activity: { one: "{count} server does not let you see its activity", other: "{count} servers do not let you see their activity" },
      backups: { one: "{count} server does not let you see its backups", other: "{count} servers do not let you see their backups" },
      alerts: { one: "{count} server does not let you see its alerts", other: "{count} servers do not let you see their alerts" },
      logs: { one: "{count} server does not let you see its logs", other: "{count} servers do not let you see their logs" },
    },
    label: "Servers that did not let you in",
    servers: "Servers:",
  },
  scope: {
    label: "Servers",
    all: "All servers",
    central: "{name}, this central",
  },
} as const;
