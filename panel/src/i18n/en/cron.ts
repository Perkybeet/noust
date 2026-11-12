/** Strings of the console's scheduled tasks: the list, the dialog that makes one and its run history. */
export const cron = {
  status: {
    succeeded: "Succeeded",
    neverRun: "Never run",
  },

  presets: {
    hourly: "Every hour",
    daily: "Every day at 02:00",
    weekly: "Every Monday at 02:00",
    monthly: "On day 1 of every month at 02:00",
    custom: "Custom calendar expression",
  },

  /** How often a task runs, as a person says it. */
  frequency: {
    hourly: "Every hour",
    daily: "Every day",
    weekly: "Every week",
    monthly: "Every month",
    custom: "Custom",
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
    title: "Scheduled tasks",
    newJob: "New task",
    what: "the scheduled tasks",
    filterAria: "Filter scheduled tasks",
    searchAria: "Search scheduled tasks",
    searchPlaceholder: "Search by name or command",
    jobsCount: { one: "{count} task", other: "{count} tasks" },
    // Always plural, whatever "shown" and "total" are: matches how the unfiltered count
    // alone decides singular/plural, so filtering never flips "1 task" into "1 of 1 tasks".
    jobsCountFiltered: "{shown} of {total} tasks",
    empty: {
      title: "Schedule your first task",
      description: "A scheduled task runs a command by itself: every hour, every night, or at the times you choose.",
    },
    noMatch: "No task matches this search.",
  },

  table: {
    captionAll: "Scheduled tasks",
    captionFiltered: "Scheduled tasks matching the filters",
    disabled: "Disabled",
    columns: {
      job: "Task",
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
    deleteJob: "Delete task",
  },

  deleteDialog: {
    description: "Removes the timer and its service. This cannot be undone.",
  },

  dialog: {
    titleNew: "New scheduled task",
    createJob: "Create task",
    saveJob: "Save task",
    more: "More options",
    previewCheckError: "The schedule could not be checked.",
    checkingSchedule: "Checking the schedule",
    noFutureRun: "This schedule has no future run.",
    systemdOutputLabel: "What systemd said",
    nextRuns: "Next runs",
    serverClock: "On the server's clock ({zone}), the one the schedule is written in.",
    serverTime: "{time} {zone}",
    yourTime: "{time} your time",
    errorCreate: "The task was not created",
    errorSave: "The task was not saved",
  },

  fields: {
    nameDescription: "Names the task and its timer, such as nightly-report.",
    nameMissing: "Enter a name for the task.",
    command: "Command",
    commandDescription: "Runs directly, not through a shell: pipes, && and $VARS do not work.",
    commandMissing: "Enter the command to run.",
    application: "Application",
    applicationDescription: "Pick one and the task runs in its folder, with its .env and tools, as its account.",
    applicationNone: "None: a task of the server",
    schedule: "Repeats",
    weekday: "Day of the week",
    day: "Day of the month",
    time: "At",
    timeInvalid: "Give a time such as 03:30.",
    calendarLabel: "Calendar expression",
    calendarDescription: "In systemd's calendar syntax, on the server's clock: Mon..Fri *-*-* 09:00:00 is weekdays at 09:00; *-*-* *:0/15 is every 15 minutes.",
    calendarMissing: "Enter a calendar expression, such as *-*-* 03:30:00.",
    user: "User",
    userDescription: "The service user set in Settings when left empty.",
    workingDirectory: "Working directory",
    workingDirectoryDescription: "Its application's folder, when it has one.",
    limit: "Stop it if it runs too long",
    limitDescription: "A task that never ends keeps its timer from starting the next run.",
    timeout: "Longest it may run",
    timeoutRange: "Give a whole number of seconds from 1 to 86400.",
    service: "Service",
    serviceDescription: "The service of the application's stack the command runs in. This server when left empty.",
  },

  /** A task that runs once, at a date, and retires itself; and the record of the ones that ran. */
  once: {
    modeLabel: "When it runs",
    repeats: "Repeats",
    once: "Once",
    date: "Date",
    dateMissing: "Choose the date.",
    zoneNote: "The time is the server's. The task retires itself once it has run.",
    previewWords: "Runs once, then retires itself",
    at: "Once, {when}",
    historyTitle: "Ran once",
    historyDescription: "One-time tasks that ran and retired themselves, kept for {days} days with how each one ended.",
    historyCaption: "One-time tasks that ran",
    historyWhat: "the one-time tasks that ran",
    columns: { task: "Task", result: "Result", due: "Asked for", ran: "Ran" },
    late: "Late",
    lateHelp: "The server was off at that moment and ran it when it came back.",
    again: "Schedule again",
    viewOutput: "See what it printed",
    outputTitle: "What {name} printed",
    outputDescription: "The last lines of its output, with credentials masked.",
    noOutput: "It printed nothing.",
    notOnServer: "{node} runs a Noust older than this console and has no one-time tasks.",
  },

  runsDrawer: {
    titleFor: "Runs of {name}",
    titleDefault: "Runs",
    description: "Newest first, read from the service's journal.",
    what: "the run history",
    empty: "This task has not run yet.",
    noExitCode: "No exit code",
    exitCode: "Exit {code}",
    output: "Output",
    outputLabel: "Output of this run of {name}",
  },

  ledger: {
    title: "{missed} missed and {late} late in the last 7 days",
    description: "A timer makes up one run for the time the server was off and says nothing about the others.",
    listLabel: "Occurrences that did not run on time",
    allRan: { one: "The one occurrence due in the last 7 days ran on time.", other: "All {count} occurrences due in the last 7 days ran on time." },
    late: "ran late: made up afterwards",
    serverOff: "did not run: the server was off",
    missed: "did not run",
  },

  toast: {
    theJob: "the task",
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
