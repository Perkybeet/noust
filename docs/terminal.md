# Terminal and one-off commands

Noust opens three things you used to need SSH for, and keeps a record of every one of them:

- **A shell as an application**, in the console: the account the application runs as, in its
  sandbox, in its directory, with its environment. For a Compose or container application, a
  shell in one of its containers.
- **A command run once** in an application, from the console or `noust app exec`: the same
  account, sandbox and environment, no shell, its output kept in a job's log.
- **A root shell on the server itself**, in the console, for administrators only, off until
  you turn it on.

A shell in the browser is an open door to an application's secrets. This page says what stands
in front of the door, what is kept of everyone who goes through it, and how to change either.

- [Using it](#using-it)
- [The security model](#the-security-model)
- [Recordings](#recordings)
- [A command run once](#a-command-run-once)
- [The server's own shell](#the-servers-own-shell)
- [When the connection drops](#when-the-connection-drops)
- [What the console's terminal does and refuses](#what-the-consoles-terminal-does-and-refuses)
- [Configuration](#configuration)
- [The ENS profile](#the-ens-profile)
- [The API](#the-api)
- [What it is not](#what-it-is-not)

## Using it

**In the console**, an application's page has **More actions > Terminal**
(`/apps/<domain>/terminal`), and **Server > More actions > Open a terminal**
(`/server/terminal`) opens the server's. The page says what you are about to open and what is
kept of it. **Open terminal** asks you to confirm it is you (sudo mode), then connects. The strip
above the screen says where you are, as whom, when the session ends however busy it is, and how
long it may sit idle.

Keys go to the program, as in any terminal, with three that are the console's:

| Keys | What they do |
|---|---|
| Ctrl+C | Interrupts the program, as always. It is never a copy. |
| Ctrl+Shift+C, Ctrl+Shift+V | Copy the selection (or the whole screen when nothing is selected) and paste. |
| Shift+Page Up, Shift+Page Down, Shift+Home, Shift+End | Scroll back through what was printed, and return to the newest. Typing returns too. |
| Escape, then Tab | Leave the terminal with the keyboard. Tab belongs to the shell, so this is the way out for someone who cannot use a pointer. The Escape still reaches the program. |

The mouse wheel scrolls back as well, and double and triple clicks select a word and a line. On a
full-screen program (an editor, a pager) the wheel sends arrow keys instead, because there is
nothing to scroll back to.

Pasting into a program that asked for it is bracketed, so a pasted newline is text and not
Enter. The text the program printed stays text: a pasted or printed escape sequence cannot make
the console copy to your clipboard, open a link, or type for you (see [What the console's terminal
does and refuses](#what-the-consoles-terminal-does-and-refuses)).

**Reading it without sight.** The screen is drawn on a canvas, which a screen reader cannot read.
**Text view** shows the same screen as text in a region the keyboard can scroll, and **Read new
output aloud** announces new lines as they appear (off by default: a program that redraws the
screen, such as an editor, would be read again each time).

**Opening again.** When a shell ends, the page says how ("The shell exited with status 3", "Closed:
nothing was typed for 10 min") and **Open a new terminal** starts another with the same choices.
**Options** chooses the shell (`bash`, `sh`, `ash`, `zsh`, `dash`; the server's default otherwise)
and, for a stack that runs several services, the service.

**From the command line**, a command run once is `noust app exec`:

```bash
noust app exec shop.example.com -- rails runner 'puts User.count'
noust app exec --service worker shop.example.com -- ./bin/status
```

The program and its arguments follow `--`. They are given to the program as they are: `-- echo
'a; b'` prints `a; b`.

## The security model

Every layer below is enforced on the server. The console only reflects them, which is why a
request that skips the console meets all of them anyway.

**Who may.** `apps.terminal` opens a terminal or runs a command in an application: administrators
and, inside their team, tenants. It is not an operator's permission, because a shell as the
application reads the secrets the application has and changes what runs. A principal limited to
some teams reaches only the applications of those teams. The server's shell needs `server.terminal`
(administrators) and a principal that sees every team. On a fleet, each node also sets a ceiling
for its central: an application's terminal needs `noust fleet authorize --access admin`, and the
server's shell needs `--allow-host-access` as well.

**Sudo mode.** Asking for a terminal, and running a command, need sudo mode, like every
destructive action: the console asks "Confirm it's you" and retries.

**Approval.** Opening a root shell on the server is one of the changes the four-eyes approvals
cover (`terminal.server`). With approvals on, a second person approves the request before the
ticket is issued.

**A ticket, once.** The call that asks for a terminal answers with a ticket: random, stored only as
a hash, valid for 20 seconds, bound to the credential that asked (and to its address, as sessions are
unless `bind_session_to_ip` is turned off), and spent the first time it is presented, even when that attempt is
refused. A ticket read out of a proxy's log is worth nothing. The WebSocket then passes the same
checks as every other: address, host, origin, the credential's own permission, the second-factor
and usage-notice gates.

**Whose shell it is.** For an application on this server, the shell is a process of the
application's own account (`noust-app-<name>`, or the account an older application already runs
as), started in a transient systemd unit with the same confinement as its builds and hooks:
the application's directory and `.env`, nothing of Noust's configuration, the store or root's
home. The environment is the application's, plus `TERM` and the locale. For a Compose or container
application, the shell is `docker exec -it` into one of its running containers, as the container's
user; Noust adds no privilege the stack does not already give its service.

**Bounded in time and number.** A session ends after `terminal.idle_minutes` with nothing typed and
after `terminal.max_minutes` in all; both are enforced by the process runner, not the browser, and
the console warns a minute before. `terminal.max_sessions` and `terminal.max_sessions_per_user`
bound how many are open. Whatever the console says, a session that no connection has attached to
for a minute is ended.

**Recorded, or not opened.** A session cannot start without a recording. See below.

**Everything is audited** (the audit log's closed catalog):

| Event | When |
|---|---|
| `terminal.ticket` | A ticket was issued. |
| `terminal.start` | A shell opened: who, where, as whom, from which address. |
| `terminal.end` | It ended: why, how long, the exit status, and the recording's digest. |
| `terminal.kill` | An administrator ended somebody else's terminal. |
| `terminal.exec` | A one-off command was queued and finished: the command, how it ended. |
| `terminal.denied` | A terminal was refused: a limit, a ticket that did not match, a switch that is off. |
| `terminal.recording.read` | Somebody read a recording. |

Administrators can see the open sessions (`GET /api/terminal/sessions`) and end one
(`DELETE /api/terminal/sessions/{id}`).

## Recordings

Each session is recorded, input and output, in an
[asciicast v2](https://docs.asciinema.org/manual/asciicast/v2/) file, so any asciinema player
plays it and nothing from Noust is needed to read it. Beside it, a small `.json` says how the
session ended and holds the SHA-256 of the cast, which the `terminal.end` audit event repeats. The
audit log is HMAC-chained, so a recording that no longer matches its digest was changed after the
fact, and `noust audit recording` says so first.

They live in `terminal-recordings/` beside the store, `0600`, and are kept for
`retention.terminal_days` (90 by default; under the ENS profile at least as long as the audit log).

- **Bounded fairly.** A recording takes at most `terminal.recording.max_mb`. Output has 80% of it
  and what was typed the rest, so a flood of output (`yes`, `cat` of a large file) fills its share
  and is replaced by one marker while what was typed keeps being recorded.
- **Scrubbed.** Every secret value Noust knows of the application (its marked variables, URLs with
  passwords) and Noust's own credentials are replaced by `***` before anything is written, including
  a value split across two reads.
- **Passwords are not recorded.** What is typed while the program has echo off in line mode (a
  password prompt) is replaced by `<hidden: the program asked for a secret>`.
- **What Noust does not know to be a secret is recorded as it was.** A recording is an audit
  trail, not a vault: reading one needs `audit.read`, and is itself audited.

```bash
noust audit recordings                       # newest first; --app, --actor, --kind app|server
noust audit recording 20261006T091203Z-1f2e3d4c5b6a7988          # what was printed and typed
noust audit recording ID --no-input          # only what was printed
noust audit recording ID --format cast       # the asciicast, for a player
```

The text form removes the program's escape sequences, because printed as they are they would be
obeyed by your own terminal.

## A command run once

`POST /api/apps/{domain}/exec`, the console's **Run a command once** and `noust app exec` are one
implementation:

- The command is an argument vector. A shell never sees it: a pipe, a redirection or `$VAR` reaches
  the program as text. The console splits what you type on spaces and quotes, shows you the list it
  will run, and says when an argument holds something a shell would have interpreted.
- It runs as the application's account in its sandbox, in its directory, with its environment. For
  a stack, in a container of the chosen service (`docker exec`, no TTY).
- From the console it is a job: its log is the output, scrubbed of the application's secrets,
  read while it runs and once more when it ends. At most 2 MiB are kept, with a marker where the
  rest was dropped. It never changes the application's state in the header, because a command is
  work done in an application, not something that happens to it.
- It stops after `--timeout` seconds (300 by default, at most `terminal.exec.max_seconds`). A
  command that exits with anything but 0, or that did not finish, is a failed job whose log says
  how, and `noust app exec` exits with the command's own status.
- In a container, stopping a command leaves the process Docker started running there: Docker has
  no way to signal it from outside. Use a timeout you can live with, and `docker compose exec` to
  look for what is left.

## The server's own shell

It is root, with nothing in between: the shell an administrator gets over SSH. That is why it is a
switch, off until you turn it on, and why the console says so on the page before opening it.

```bash
noust config set terminal.server.enabled true
```

Under the [ENS profile](#the-ens-profile) it stays off even then, unless you also force it, which
is put on record:

```bash
noust config set terminal.server.force_ens true
```

When it is off the page says so, and with the command that changes it. Everything else in
[The security model](#the-security-model) applies, with the four-eyes approval on top.

## When the connection drops

A WebSocket that drops does not end the shell. The server keeps it for 60 seconds, holding up to
256 KiB of what it printed meanwhile, and the console asks for a new ticket that resumes the same
session, with the backoff every stream of the console uses. The page says "Reconnecting", and on
return "Reconnected to the same shell" and, if the output outgrew the backlog, that some of it was
lost. A reload within the minute finds the shell. After the minute the session is ended and
recorded as such.

Closing the tab does not end the shell either: **End session** does. A session ends at once when
the credential it was opened with stops being valid (the session was signed out, the account
disabled).

## What the console's terminal does and refuses

The console cannot use xterm.js: its content security policy forbids the `<style>` element the
library injects. The terminal is its own, in `panel/src/lib/terminal/` (the emulator) and
`panel/src/components/ui/terminal/` (the drawing on a `<canvas>`, set through the CSSOM).

It understands the subset of xterm's sequences real programs use: cursor movement and saving, erase,
insert and delete of lines and characters, scroll regions, the alternate screen, tab stops, the
16, 256 and true colours of SGR with bold, dim, italic, underline, inverse, hidden and strike, the
DEC line-drawing set, and the modes that change what is drawn or what keys send (application
cursor keys, origin, auto-wrap, bracketed paste). Wide and combining characters take the cells they
should.

It refuses, because each is a way for output to act on the person reading it:

- It never writes your clipboard (OSC 52) and never opens a link (OSC 8).
- It never reports the window's title or size in pixels back as input (`CSI 21 t`, `CSI 14 t`),
  the replies a hostile file once used to type into a shell.
- It answers only queries whose answer is fixed or a position: device status, cursor position and
  the two device attribute reports.
- It never sends mouse reports. A program that asks for the mouse gets none, and the console's own
  selection keeps working.
- A paste can never close its own bracket: an end-of-paste sequence inside the pasted text is
  removed.
- A sequence it does not know is ignored in bounded memory: an unterminated string is dropped
  after 4,096 characters, and a run of ignored bytes after 65,536.

The colours are the console's own tokens, read where the screen is drawn and read again when the
theme changes, so a program's red is the console's failure red in both themes.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `terminal.enabled` | `true` | Whether the console may open a terminal into an application. Off, one-off commands remain. |
| `terminal.idle_minutes` | `10` | Nothing typed for this long ends a terminal. 1 to 120. |
| `terminal.max_minutes` | `120` | The longest a terminal lasts, however busy. 1 to 720. |
| `terminal.max_sessions` | `8` | Terminals open at once. 1 to 64. |
| `terminal.max_sessions_per_user` | `3` | Terminals one person may have open. 1 to 16. |
| `terminal.recording.max_mb` | `8` | The most one session's recording may take. 1 to 1024. |
| `terminal.server.enabled` | `false` | A root shell on the server in the browser. |
| `terminal.server.force_ens` | `false` | Allows it under the ENS profile, on record. |
| `terminal.exec.max_seconds` | `1800` | The longest a one-off command may run. 1 to 86400. |
| `retention.terminal_days` | `90` | Days recordings are kept. |

A value that cannot be read falls back to its default: a limit is never the thing that fails closed
into "no limit".

## The ENS profile

Under `security.profile: ens-medium` the limits are capped and the server's shell is closed:

| | Standard | ENS |
|---|---|---|
| Idle limit | as configured | 10 minutes at most |
| Total limit | as configured | 60 minutes at most |
| The server's root shell | when `terminal.server.enabled` | refused unless `terminal.server.force_ens` as well, which is audited; with approval |
| Recordings kept | `retention.terminal_days` | at least as long as the audit log |

Every session is recorded in either profile, so the controls op.acc.6.4 (session limits) and
op.exp.8 (records of activity) are met by the terminal as they are by the console.

## The API

| Route | Permission | |
|---|---|---|
| `POST /api/apps/{domain}/terminal` | `apps.terminal`, sudo mode | A ticket for a terminal in an application. Body: `rows`, `cols`, `shell`, `service`, `resume`. |
| `POST /api/server/terminal` | `server.terminal`, sudo mode, approval | The same for the server. |
| `POST /api/apps/{domain}/exec` | `apps.terminal`, sudo mode | Queues a one-off command (`argv`, `service`, `timeout`) and answers 202 with the job. |
| `GET /api/terminal/status` | `apps.read` | The switches and limits, which the console reads to offer a terminal or say why there is none. |
| `GET /api/terminal/sessions`, `DELETE /api/terminal/sessions/{id}` | `apps.terminal` | Open sessions in the caller's teams; end one. |
| `GET /api/terminal/recordings`, `GET /api/terminal/recordings/{id}` | `audit.read` | Recordings and one with its events and verdict. |
| `WS /ws/terminal?terminal_ticket=...` | `apps.terminal` | The session. Authenticated like every WebSocket of the console. |

The socket speaks JSON text frames. The client sends `input` (`data`), `resize` (`rows`, `cols`),
`ping` and `close`. The server sends `ready` (who, where, `ends_in`, whether this is a `resumed`
session, whether output was `lost`), `output` (`data`), `notice` (a limit `kind` and the seconds
left), `ended` (`reason`, `exit_code`), `error` (`code`, `message`) and `pong`. A console that cannot
keep up makes the program wait instead of making the server hold the output in memory.

On a central, `/ws/nodes/{node}/terminal` and the node's routes are relayed over the node's tunnel:
a central re-implements nothing a node does, and the node's own permissions and ceilings apply.

## What it is not

- **Not a replacement for SSH.** It is a shell for the work an operator does through a console. A
  session is bounded in time, recorded, and ends when the console does.
- **Not a boundary between applications that share an account**, any more than the rest of Noust
  is: a shell as an application can do what the application can.
- **Not tamper-proof against root on the machine.** A recording's digest is in a chained log, which
  makes changing it detectable, not impossible; ship the audit log off the machine for that
  (`audit.syslog`, `audit.journald`).
- **No file transfer, no port forwarding, no multiplexing.** One session is one shell.
