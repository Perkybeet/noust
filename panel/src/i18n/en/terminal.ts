/**
 * The terminal and the one-off command (features/terminal, components/ui/TerminalScreen).
 *
 * What a shell prints is the system's and is never translated; what is here is the words around
 * it: what the session is, how it ends, what to do.
 */
export const terminal = {
  /** The canvas and what a screen reader gets in its place (components/ui/TerminalScreen). */
  screen: {
    hint: "Type to send keys to the shell. Ctrl+C interrupts it. Copy with Ctrl+Shift+C and paste with Ctrl+Shift+V. Shift+Page Up scrolls back. To leave the terminal with the keyboard, press Escape and then Tab.",
    textView: "Terminal contents as text",
    announcements: "New terminal output",
    jump: "Jump to the latest output",
  },
  document: {
    app: "Terminal - {domain}",
    server: "Terminal - the server",
  },
  /** The strip above the screen. */
  header: {
    label: "Terminal session",
    as: "as",
    endsIn: "Ends in {time}",
    idleLimit: "Closes after {minutes} min without typing",
    recorded: "Recorded in the audit trail",
    root: "root",
    container: "the container's user",
  },
  state: {
    opening: "Opening",
    open: "Connected",
    reconnecting: "Reconnecting",
    ended: "Ended",
    failed: "Not opened",
  },
  actions: {
    open: "Open terminal",
    opening: "Opening...",
    end: "End session",
    newSession: "Open a new terminal",
    copy: "Copy",
    textView: "Text view",
    textViewHelp: "Show the screen as text, which a screen reader can read.",
    announce: "Read new output aloud",
    announceHelp: "A program that redraws the screen, such as an editor, is read again each time.",
    options: "Options",
    shell: "Shell",
    shellDefault: "The server's default",
    service: "Service",
    serviceHelp: "The container to open it in. Leave it empty when the stack runs one service.",
  },
  /** Before the session opens. */
  start: {
    app: {
      title: "A shell as {domain}",
      description:
        "You get a shell as the account the application runs as, in its sandbox and with its environment: the same files and the same secrets it has, and nothing else on the server. A stack's shell is a shell in its container.",
    },
    server: {
      title: "A root shell on the server",
      description:
        "You get the shell an administrator gets over SSH, as root, with nothing in between. It can change anything on this machine.",
    },
    recorded:
      "Everything you type and everything it prints is kept in the audit trail, with who opened it. It ends after {idle} min without typing and after {max} min in all.",
    sudo: "Opening it asks you to confirm it's you.",
    permission: "Your account cannot open a terminal here. It needs the {permission} permission.",
  },
  page: {
    title: "Terminal",
    loading: "Loading the terminal",
  },
  /** Terminals as a whole are off (`terminal.enabled`). */
  disabled: {
    title: "Terminals are off on this server",
    description:
      "An administrator turned the console's terminals off, so no shell opens from the browser. A command can still be run once, below.",
    enable: "Turn them on from a terminal on the server:",
  },
  /** The server's shell is a switch, off until an administrator turns it on. */
  off: {
    title: "The terminal on the server is off",
    description:
      "A root shell in the browser is the most powerful thing the console can open, so it stays off until an administrator turns it on.",
    ens: "The ENS profile keeps it off even when it is turned on, unless it is forced as well, which is put on record.",
    enable: "Turn it on from a terminal on the server:",
    force: "To allow it anyway, which is put on record:",
  },
  /** Said under the screen while it lasts. */
  notice: {
    idle: "Nothing has been typed for a while. This terminal closes in {seconds} s unless you press a key.",
    limit: "This session reaches its time limit in {seconds} s.",
    lost: "Some of what the program printed was lost while the connection was down.",
    resumed: "Reconnected to the same shell.",
  },
  /** How a session ended, as a sentence for the person. */
  ended: {
    exited: "The shell exited.",
    exitedWith: "The shell exited with status {code}.",
    idle: "Closed: nothing was typed for {minutes} min.",
    limit: "Closed: the session reached its time limit.",
    closed: "The session was ended.",
    killed: "An administrator ended this session.",
    revoked: "Closed: the sign-in this session was opened with is no longer valid.",
    disconnected: "Closed: the connection was gone for more than a minute.",
    shutdown: "Closed: the console is stopping.",
    unknown: "The session ended.",
  },
  failed: {
    title: "The terminal did not open",
    connection: "The connection to the terminal closed before it opened.",
    unreachable: "The terminal could not be reached.",
  },
  /** One command, run once (features/terminal/ExecPanel). */
  exec: {
    title: "Run a command once",
    description:
      "The command runs as the application, in its sandbox, with its environment, and its output is kept in the job's log. Nothing stays open afterwards.",
    commandLabel: "Command",
    commandPlaceholder: "rails runner 'puts User.count'",
    commandHelp:
      "The program and its arguments, separated by spaces; quotes keep spaces together. It is run as it is, not by a shell: a pipe, a redirection or a $variable is passed to the program as text.",
    unclosed: "A quote is open: the command ends inside it.",
    argvLabel: "Run as",
    shellSyntax: "These are passed as text, not interpreted: {words}",
    serviceLabel: "Service",
    serviceHelp: "For a stack that runs several services: the one to run it in.",
    timeoutLabel: "Time limit",
    timeoutHelp: "Seconds. It is stopped when it runs out.",
    run: "Run command",
    running: "Running",
    again: "Run another",
    outputLabel: "Log of the command in {domain}",
    outputEmpty: "Nothing was logged.",
    outputWaiting: "Waiting for the first line",
    queuedTitle: "Running a command in {domain}",
    failedTitle: "The command in {domain} failed",
    succeededTitle: "The command finished",
    exitStatus: "Exit status {code}.",
    cannotQueue: "The command could not be started",
    viewActivity: "View in Activity",
    hint: "From a terminal:",
    previewEmpty: "Type a command to see how it will run.",
    timeoutInvalid: "Whole seconds, from 1 to {max}.",
    optionsLabel: "Service and time limit",
    timeoutPlaceholder: "Up to {seconds}",
  },
} as const;
