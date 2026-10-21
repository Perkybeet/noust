/**
 * A Docker Compose stack's file, profiles and extra arguments, and the application as a
 * declaration file. What docker compose prints is shown verbatim and is not here.
 */
export const composeSettings = {
  title: "Compose file, profiles and arguments",
  description: "What the stack is built and started with. A new file or new profiles are written to its unit too, so a start at boot brings up the same stack.",
  loading: "Loading the Compose settings",
  readFailed: "Could not read the Compose settings",
  file: {
    label: "Compose file",
    description: "Relative to the project. Left empty, the file the unit recorded, or the usual names, is used.",
    placeholder: "deploy/compose.prod.yml",
  },
  profiles: {
    label: "Profiles",
    description: "Profiles to activate, separated by spaces or commas. A service under a profile that is not listed is left out of every build and start.",
  },
  buildArgs: {
    label: "Arguments of docker compose build",
    description: "Only these are accepted: {allowed}.",
  },
  upArgs: {
    label: "Arguments of docker compose up",
    description: "Only these are accepted: {allowed}. Noust decides when to build, so --build and --no-build are not.",
  },
  services: {
    label: "Services",
    description: "Build and start only these (and what they depend on). Empty means every service.",
  },
  saved: "The Compose settings of {domain} were saved. They apply to the next update.",
  notSaved: "The Compose settings of {domain} were not saved",
  reset: "Forget these choices",
  resetTitle: "Forget the Compose choices of {domain}?",
  resetDescription: "The file and profiles the unit recorded apply again, and no extra arguments are added.",
  resetAction: "Forget them",
  resetDone: "{domain} uses what its unit recorded",
  networks: {
    title: "Docker networks",
    description: "The networks the stack is on and who else is on them, as Docker reports it. Read only.",
    loading: "Loading the stack's networks",
    readFailed: "Could not read the stack's networks",
    none: "The stack is on no Docker network now. Its containers are not running.",
    isolated: "Isolated: every network is the stack's own and nothing else is on it.",
    shared: "Not isolated: a network is shared with something that is not part of this stack.",
    list: "Networks of the stack",
    members: "Containers on {name}",
    ranges: "Address ranges: {ranges}",
    internal: "Internal",
    sharedBadge: "Shared",
    other: "Other stack",
  },
  fromTerminal: "The same, from a terminal",
  page: {
    title: "Compose",
    description: "What the stack is built and started with, the Docker networks it is on and the registry accounts it pulls with.",
  },
} as const;
