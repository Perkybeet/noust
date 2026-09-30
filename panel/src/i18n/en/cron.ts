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
    hourly: "Every hour",
    daily: "Every day at 02:00",
    weekly: "Every Monday at 02:00",
    monthly: "On day 1 of every month at 02:00",
    custom: "Custom calendar expression",
  },

  // A calendar expression in words. Times are the server's clock, as the timer reads it.
  words: {
    everyHour: "Every hour, on the hour",
    everyMinutes: { one: "Every minute", other: "Every {count} minutes" },
    everyDayAt: "Every day at {time}",
    workdaysAt: "Monday to Friday at {time}",
    everyWeekdayAt: "Every {weekday} at {time}",
    monthlyAt: "On day {day} of every month at {time}",
    custom: "Custom schedule",
  },

  page: {
    title: "Cron",
    description: "Commands this server runs on a schedule.",
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
      description: "A cron job runs a command by itself: every hour, every night, or at the times you write.",
    },
    noMatch: "No job matches this search.",
  },

  table: {
    captionAll: "Cron jobs",
    captionFiltered: "Cron jobs matching the filters",
    actionsFor: "Actions for {name}",
    disabled: "Disabled",
    columns: {
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
    description: "Runs a command on this server by itself, on a schedule.",
    createJob: "Create job",
    saveJob: "Save job",
    previewCheckError: "The schedule could not be checked.",
    checkingSchedule: "Checking the schedule",
    noFutureRun: "This schedule has no future run.",
    systemdOutputLabel: "What systemd said",
    nextRuns: "Next runs",
    serverClock: "On the server's clock ({zone}), the one the schedule is written in.",
    serverTime: "{time} {zone}",
    yourTime: "{time} your time",
    errorCreate: "The job was not created",
    errorSave: "The job was not saved",
  },

  fields: {
    name: "Name",
    nameDescription: "Names the job and its timer, such as nightly-report.",
    nameMissing: "Enter a name for the job.",
    command: "Command",
    commandDescription: "Runs directly, not through a shell: pipes, && and $VARS do not work.",
    commandMissing: "Enter the command to run.",
    schedule: "Schedule",
    calendarLabel: "Calendar expression",
    calendarDescription: "In systemd's calendar syntax, on the server's clock: Mon..Fri *-*-* 09:00:00 is weekdays at 09:00; *-*-* *:0/15 is every 15 minutes.",
    calendarMissing: "Enter a calendar expression, such as *-*-* 03:30:00.",
    user: "User",
    userDescription: "Blank runs it as the service user set in Settings.",
    workingDirectory: "Working directory",
    workingDirectoryDescription: "Blank uses its application's folder, when it has one.",
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
