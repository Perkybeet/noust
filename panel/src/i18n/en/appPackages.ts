/**
 * The system packages an application needs on the host (its Build commands settings): an image
 * library, a font, a converter. What the package manager prints is shown verbatim and is not here.
 */
export const appPackages = {
  title: "System packages",
  description: "Libraries and tools of the server this application needs, such as libvips-dev, ffmpeg or a font. Noust installs them before the next build; a repository cannot ask for one.",
  loading: "Loading the system packages",
  readFailed: "The system packages could not be read",
  fromTerminal: "From a terminal",
  listLabel: "Packages this application needs",
  none: "This application declares no system packages.",
  hasIt: "Installed",
  missing: "Not installed yet",
  notChecked: "Not checked",
  refused: "Not allowed",
  refusedTitle: "The server no longer allows these packages",
  refusedBody: "{names} fall outside the packages this server lets applications declare. Remove them from the list, or ask the security officer to extend the policy.",
  unsupportedTitle: "This server's packages are not managed by Noust",
  unsupportedBody: "It installs packages through transactions and a reboot, or has no apt, dnf or zypper. The list is kept, but install the packages by hand.",
  field: "Packages",
  fieldDescription: "One distribution package name per line, such as libvips-dev. No versions and no options. Packages are added, never removed.",
  fieldPolicy: "One name per line. This server allows: {allowed}.",
  placeholder: "libvips-dev\nffmpeg",
  save: "Save the list",
  saved: "The system packages of {domain} were saved",
  notSaved: "The system packages of {domain} were not saved",
  install: "Install what is missing",
  installed: "The system packages of {domain} are installed",
  notStarted: "The installation for {domain} did not start",
  notInstalled: "The system packages of {domain} were not installed",
  jobFailedSilently: "The installation failed without saying why. The job's log has the package manager's output.",
  when: "Saving records the list. The packages missing are installed before the next update builds, or now with the button; an update stops, and what serves keeps serving, if they cannot be installed.",
} as const;
