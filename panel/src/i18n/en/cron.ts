/** Strings of the console's cron area: the jobs list, its dialog and run history. */
export const cron = {
  area: "Cron jobs",

  common: {
    cancel: "Cancel",
    edit: "Edit",
    clearFilters: "Clear filters",
    fromTerminal: "From a terminal",
  },

  status: {
    succeeded: "Succeeded",
    failed: "Failed",
    unknown: "Unknown",
    neverRun: "Never run",
  },

  presets: {
    hourly: "Hourly",
    daily: "Daily",
    weekly: "Weekly",
    monthly: "Monthly",
    custom: "Custom",
    dailyAt: "Every day at {hours}:{minutes}",
  },

  page: {
    title: "Cron",
    description: "Commands run on a schedule, as systemd timers.",
    newJob: "New job",
    loadError: "Could not load cron jobs",
    filterAria: "Filter cron jobs",
    searchAria: "Search cron jobs",
    searchPlaceholder: "Search by name or command",
    jobsCount: { one: "{count} job", other: "{count} jobs" },
    // Always plural, whatever "shown" and "total" are: matches how the unfiltered count
    // alone decides singular/plural, so filtering never flips "1 job" into "1 of 1 jobs".
    jobsCountFiltered: "{shown} of {total} jobs",
    empty: {
      title: "Schedule your first job",
      description: "A cron job runs a command on a schedule: hourly, daily, weekly, monthly, or a systemd calendar expression.",
    },
    noMatch: {
      title: "No job matches",
      description: "Nothing on this machine matches these filters.",
    },
  },

  table: {
    captionAll: "Cron jobs",
    captionFiltered: "Cron jobs matching the filters",
    actionsFor: "Actions for {name}",
    disabledReason: "Disabled",
    enabled: "Enabled",
    disabled: "Disabled",
    columns: {
      state: "State",
      job: "Job",
      schedule: "Schedule",
      nextRun: "Next run",
      lastResult: "Last result",
    },
  },

  actions: {
    runNow: "Run now",
    viewRuns: "View runs",
    disable: "Disable",
    enable: "Enable",
    deleteJob: "Delete job",
  },

  deleteDialog: {
    title: "Delete {name}",
    description: "Removes the timer and its service unit. This cannot be undone.",
  },

  dialog: {
    titleNew: "New cron job",
    titleEdit: "Edit {name}",
    description: "Runs a command on a schedule, as a systemd timer.",
    createJob: "Create job",
    saveJob: "Save job",
    previewCheckError: "The schedule could not be checked.",
    checkingSchedule: "Checking the schedule",
    noFutureRun: "This schedule has no future run.",
    systemdOutputLabel: "What systemd said",
    errorCreate: "The job was not created",
    errorSave: "The job was not saved",
  },

  fields: {
    name: "Name",
    command: "Command",
    commandDescription: "Run as an argv, without a shell.",
    schedule: "Schedule",
    calendarLabel: "Calendar expression",
    calendarDescription: "A systemd OnCalendar expression.",
    user: "User",
    userDescription: "Defaults to the configured service user.",
    workingDirectory: "Working directory",
  },

  runsDrawer: {
    titleFor: "Runs of {name}",
    titleDefault: "Runs",
    description: "Newest first, read from the unit's journal.",
    loadError: "Could not load the run history",
    loading: "Loading run history",
    empty: "This job has not run yet.",
    noExitCode: "No exit code",
    exitCode: "Exit {code}",
    output: "Output",
    outputLabel: "Output of this run of {name}",
  },

  toast: {
    theJob: "the job",
    created: "Created {name}",
    nextRun: "Next run: {value}.",
    deleted: "Deleted {name}",
    deleteError: "Deletion of {name} failed",
    started: "Started {name}",
    startedDescription: "Its result will appear in its run history shortly.",
    startError: "Could not start {name}",
    enabled: "Enabled {name}",
    enableError: "Could not enable {name}",
    disabled: "Disabled {name}",
    disableError: "Could not disable {name}",
  },
} as const;
