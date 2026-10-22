/** The map of this server: how names, applications, databases, scheduled work and data fit together. */
export const map = {
  title: "Map",
  queryLabel: "the map of this server",
  label: "How this server fits together",
  layers: {
    names: "Names",
    applications: "Applications",
    databases: "Databases",
    scheduled: "Scheduled work",
    data: "Data",
  },
  relation: {
    serves: "answers on",
    uses: "uses",
    runs: "runs",
    keeps: "keeps",
  },
  disabled: "Disabled",
  legend: "Each line is a relation, not traffic: a name leads to the application that answers on it, and an application to the databases it uses, the scheduled work it runs and the data it keeps.",
  summary: "Applications: {applications}. Databases: {databases}. Scheduled jobs: {scheduled}.",
  truncated: "The map is larger than it draws: the rest is left out. Narrow it to a team or a project with the switcher at the top.",
  emptyTitle: "Nothing to map yet",
  emptyDescription: "Deploy an application and its names, databases, scheduled work and data appear here, connected.",
  fromTerminal: "Read it from a terminal",
} as const;
