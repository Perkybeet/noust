/**
 * The deploy queue and cancelling a deployment: what builds, what waits and for what, why a
 * request was refused, and what cancelling does. What the queue says about a job
 * (`waiting_for`) and what the server answers to a refused cancel are the system's words and
 * are shown as they come.
 */
export const queue = {
  title: "Deploy queue",
  titleApp: "Queue of {domain}",
  description: "Builds of different applications run side by side, up to the server's limit. The rest wait their turn, in order, and nothing is lost.",
  descriptionApp: "One application builds one thing at a time: what is asked meanwhile waits here, in order.",
  caption: "Jobs building or waiting, in the order they run",
  captionApp: "Jobs of {domain} building or waiting, in the order they run",
  readFailed: "Could not read the deploy queue",
  limits: {
    builds: "{running} of {max} builds running",
    waiting: "{waiting} of {limit} waiting",
  },
  columns: {
    job: "Job",
    state: "State",
    place: "Place",
  },
  place: {
    next: "Next in line",
    position: "Place {position} in line",
    since: "Running since {time}",
    none: "Not placed in the line yet",
  },
  state: {
    stopping: "Stopping",
  },
  merged: {
    one: "1 request merged into this one",
    other: "{count} requests merged into this one",
  },
  waitingForLine: "Waiting for {what}",
  cancel: {
    label: "Cancel {name}",
    stopping: "Stopping…",
    stoppingNote: "It was asked to stop and is winding down. The queue moves on when it has.",
    reasons: {
      activating: "Its release is being activated, which cannot be stopped halfway. It finishes, or puts back what served before, by itself.",
      buildsInPlace: "This application builds in the folder that serves, or in Docker for a Compose stack. Stopping either halfway would leave it broken, so it finishes by itself.",
      unsupported: "A job of this kind is halfway through changing the system and cannot be stopped safely. Wait for it to finish.",
    },
    queued: {
      title: "Take {name} out of the queue?",
      description: "It has not started. Cancelling removes it from the line and nothing on the server changes. The jobs behind it move up.",
      action: "Remove from queue",
    },
    running: {
      title: "Stop the build of {domain}?",
      description:
        "The build stops and the release it was making is removed. What serves now is not touched, and the next request in the queue starts. This works until its release starts to be activated; after that it finishes by itself.",
      action: "Stop build",
    },
    done: {
      queued: "{name} was taken out of the queue",
      stopping: "{name} was asked to stop",
    },
    deploymentCancelled: "Cancelled by {who}: the build stopped, and what served before was not touched.",
    deploymentCancelledAnonymous: "Cancelled: the build stopped, and what served before was not touched.",
    deploymentCancelledTitle: "This deploy was cancelled",
    announced: "Deploy {id} was cancelled",
  },
  full: {
    hint: "Builds run a few at a time and the rest wait their turn. Try again in {seconds} seconds, or cancel what no longer needs to wait.",
    hintNoWait: "Builds run a few at a time and the rest wait their turn. Try again in a moment, or cancel what no longer needs to wait.",
  },
  joined: {
    title: "{domain} was already waiting for an update",
    body: "This request joined it: one update builds the branch as it is when its turn comes, so nothing is lost and nothing builds twice.",
  },
} as const;
