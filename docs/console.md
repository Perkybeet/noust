# The console

The Noust console is the browser interface to a server running Noust. It is a single-page
application built into the package and served by `noust web start`: no Node, no build step
and no CDN at install time, and nothing is loaded from any other origin at run time. It is a
client of the same [API](api.md) scripts use, so everything on screen is something the API
can do.

![Overview](assets/console/overview.png)

## Starting it

```bash
noust web start           # foreground, 127.0.0.1:8080; prints the access token; Ctrl+C stops it
noust web start -d        # background until 'noust web stop' or the next reboot; prints the token
noust web enable          # a systemd service: survives reboots; prints the token
noust web token --new     # issue a token to sign in with
noust web status          # running or not, and whether as the service or in the background
noust web stop
```

Every start issues a new access token and prints it once, in the same banner whichever way
the console runs: in the foreground, in the background with `-d` (the parent process prints
it before handing the server over to the child), or as a service with `noust web enable`. It
is never silently issued unseen, and systemd starting the service again (at boot, after a
failure, after an upgrade) issues none: it keeps serving the token `noust web enable`
printed. `noust web token` without options only reports whether a token is issued: the token
itself is stored as a salted hash and cannot be shown again. A running console reads the
token from disk on every request, so issuing a new one with `noust web token --new` retires
the old one at once, with no restart needed; see [security.md](security.md#authentication)
for more, including what `--regenerate` invalidates beyond the token itself.

A foreground console runs until you press Ctrl+C or close the SSH session it was started
from; its banner says so, and prints the `noust web enable` line that would keep it running
with the same options. `noust web start -d` runs it as a background process, with its log in
`/var/log/noust/web.log` and its PID in `/run/noust-web.pid`: it does not start again
after a reboot. `noust web restart` takes the same options as `start` and does not remember
the ones used before: pass them again.

### Keep it running

`noust web enable` runs the console as a systemd service, `noust-web.service`: started now,
started again at every boot, and restarted if it fails.

```bash
noust web enable                                  # 127.0.0.1:8080, reached over SSH
noust web enable --host 0.0.0.0 --self-signed     # any option 'noust web start' takes
noust web status                                  # Runs as: noust-web.service (starts at boot)
journalctl -u noust-web                           # its log
noust web disable                                 # stop it, disable it and remove the unit
```

- It takes the options `noust web start` takes, checked by the same rules: binding beyond
  loopback without TLS is refused here too, before anything is written. There is no `-d`:
  systemd keeps it running.
- It writes `/etc/systemd/system/noust-web.service`, whose `ExecStart` is this machine's
  `noust` binary running the console in the foreground with those options, then enables and
  starts it, waits until it listens, and only then prints the access token and how to reach
  it. If it does not come up, the journal is shown instead, and no token is issued.
- The token is printed on your terminal by `noust web enable`, never by the service: the
  service's output is the journal, which more accounts can read than root. The unit file
  holds no credential either. A lost token is replaced with `noust web token --new`, which
  the running service accepts at once.
- To change the options, run `noust web enable` again with the new ones: the unit is
  rewritten and the service restarted. `noust web disable` removes it; the token and the
  console's state under `/etc/noust` stay.
- While the service runs, `noust web start` and `noust web restart` refuse to start a second
  console and name the service, and `noust web stop` points at `noust web disable` (or
  `systemctl stop noust-web`, which stops it until the next boot).
- A console already running with `-d` has to be stopped first (`noust web stop`), so the
  service can take its port.
- The service runs as root with systemd's own `PATH`, like every other unit. The unit's
  hardening leaves everything the console does as root intact (installing packages, writing
  `/etc`, deploying into `/var/www`); the template explains each directive.
- Upgrading the package restarts a running `noust-web.service` onto the new version;
  removing the package stops and disables it.

If the console's Python packages are missing, `noust web start` says which and offers to
install them; `noust web install --apt` or `--pip` installs them directly.

### Reaching it

The console acts as root, so it listens on loopback unless you give it TLS.

**Over SSH (the default and the safest).** Leave it on `127.0.0.1` and forward the port from
your own machine. `127.0.0.1` answers on the server itself and nowhere else, so opening the
server's address in a browser will not reach it; the banner prints the exact line to run:

```bash
ssh -L 8080:127.0.0.1:8080 root@server.example.com
# then open http://localhost:8080
```

**With TLS served by Noust.** Binding to anything but loopback requires a certificate:

```bash
noust web enable --host 0.0.0.0 --tls-cert /etc/letsencrypt/live/panel.example.com/fullchain.pem \
                               --tls-key /etc/letsencrypt/live/panel.example.com/privkey.pem
noust web enable --host 0.0.0.0 --self-signed   # minted under /etc/noust/panel-tls, reused while valid
```

**Behind a reverse proxy that terminates TLS.** Keep the console on loopback and declare the
proxy, so the session cookie is marked `Secure` and client addresses are the real ones:

```bash
noust web enable --trusted-proxy 127.0.0.1
```

Every example here works with `noust web start` as well, for a console that should not
outlive the session.

`--allow-ip ADDR/CIDR` (repeatable) restricts who may connect at all. `--insecure-http` serves
cleartext beyond loopback; the token and the session cookie then cross the network
unencrypted. See [security.md](security.md) for the full rules.

### Signing in

![Sign in](assets/console/login.png)

Sign in with your account: username, password and a code from your authenticator app, or a
passkey on its own. Browsers offer passkeys only on a certificate they trust, so over an SSH
tunnel or with a self-signed certificate the page says why the button is missing. The page does
not show the host name or the version before you sign in; `auth.login_label` sets a line that
tells your servers apart. After signing in, the console says when and from where you last signed
in and how many attempts failed since.

**A server with no accounts** signs in with the access token, as in 3.0, and then opens **Create
the first account**: an administrator, and (recommended) a security officer for another person.
Once an account exists, the access token is emergency access: it still signs in, and each use is
recorded. An invited person opens the invitation link, chooses a password and sets up a second
factor. Every account sets up a second factor, an authenticator app or a passkey, at its first
sign-in, before it can do anything else. When `auth.notice.text` is set, a notice of rights and
obligations is shown and must be accepted.

A session ends after 30 minutes without activity and 12 hours after sign-in (`auth.session.*`).
Five failed attempts lock the account for 15 minutes, and five from one address lock that
address; the page counts down.

## Layout

- **Sidebar**, in two groups: Overview, Applications, Databases, Domains and certificates,
  Backups; then Server, Cron, Activity. On a central, Fleet comes first. Settings is at the
  bottom. On a narrow screen it becomes a menu.
- **Top bar**: on a central, a server selector with three contexts: one server, **All servers**
  (the Fleet pages) and **This central** (the central's own settings). `/n/<server>/...` keeps
  the choice across a reload, the last server is remembered, and every page for one server shows
  its name above the title. Then the machine strip (host name, uptime, load, CPU, memory, disk,
  units running and failed), the command palette, the language switch, and the session menu
  (account, theme, keyboard shortcuts, sign out).
- The browser tab is titled `<page> - <hostname> - Noust`.

Every page follows one of the templates in [DESIGN.md](DESIGN.md): a list, a detail page with
tabs, settings with a side navigation, the dashboard, a wizard, a file editor, or the sign-in
column. Colour means state and nothing else: green running, amber in progress, red failed, grey
stopped, and violet for what you can interact with; chart series have a palette of their own.
Every state also has a shape and a text label. When a system tool fails, its own output is shown
verbatim in a monospace block, with the suggested fix above it.

## Pages

### Overview

How this server is doing, what needs you and what happened lately. Six key figures:
applications running and failed, deploys today, certificates due, backups in the last 24 hours,
disk, and pending system updates. Below them, what needs attention (worst first, each with the
way to fix it) beside recent activity as a timeline, then the machine's charts and quick actions.
When the charts' history is not being recorded, it says why and what to run. A server with
nothing deployed shows first steps instead. On a hub, the home page is the fleet summary.

### Fleet

A [central's](CENTRAL.md) own pages, hidden on a plain server. Tabs: **Summary** (the fleet's
key figures and what needs attention on every server, worst first), **Servers** (reachability,
version, load, memory, disk, labels), **Applications**, **Certificates**, **Backups**,
**Updates** and **Activity**, each row with its server and a link to its page there. A server
that did not answer, is too old for a view, or refused you is one row with its state and its
own words; the rest of the page is unaffected.

**Bulk actions** (renew certificates, back up, verify backups, update or restart applications,
update Noust, apply system updates) are chosen from a list of servers or by label, show their
plan first (which servers, in which batches, what is skipped and why), and run as a fleet job
with each server's outcome and a button to retry the servers that failed. **Add a server**
starts the same flow as **Settings > Servers**.

![Fleet](assets/console/fleet.png)

The server selector switches every other page to one server, and every action taken there
(including a destructive one, still behind "Confirm it's you") reaches that server's own API
through the central's tunnel. What the server's ceiling for the central does not allow is
greyed out with the reason. A hub (`central.role = hub`) deploys nothing of its own; switched to
a server, Applications (and every other page) is that server's own:

![A node's applications](assets/console/node-apps.png)

### Applications

![Applications](assets/console/apps.png)

Every application with its state, type, port, last deploy, CPU and memory, filterable by
state and type. On a phone, rows become cards with their actions always visible.

**New application** (`/apps/new`) walks through **Server** (on a fleet only: which server
deploys it, so any server can be reached from one place), **Source** (a Git URL, a GitHub
repository, a directory on the server, a recipe or an export, and a branch), **Address**,
**Configuration**, **Variables**, **Database** (optional: one created and linked before the first
build, its connection string in the app's environment) and **Deploy**. The server inspects the repository before anything is created: the
detected type (editable), install, build and start commands, port, and the variables declared in
`.env.example` as a form, with secrets flagged. It checks the domain's DNS, then deploys and
streams the build log until the application answers. Switched to a server of the fleet, the
wizard deploys there.

![New application](assets/console/apps-new.png)

### One application

The header shows the domain, state, type, port and a link to the live site. A banner between the
header and the tabs appears while a job runs on it, when its last deploy failed or when it is
down, with a link to **Diagnose**. Tabs:

| Tab | What it has |
|---|---|
| Overview | Last deploy, uptime, certificate, domains, webhook, runtime facts, and resource use against its limits |
| Deployments | Deployment history; for an application with instant rollback, its releases with their status, and **Roll back to this** or **Activate** on each one on disk |
| Logs | The unit's journal, live |
| Metrics | CPU and memory (requests and errors for a static site) over an hour, a day, a week or a month, against its limits, with deploys marked; or why there is nothing to show |
| Environment | The `.env`, redacted; revealing and editing need sudo mode. Changes are staged and reviewed before saving; a restart applies them |
| Database | The databases it uses, creating and linking one, and its connection string |
| Domains | Primary, aliases and redirects, with a DNS check per name; add a name as an alias or a redirect |
| Settings | One page per subsection, below |

![Application](assets/console/app-overview.png)

Each deployment has its own page: commit, trigger, duration, a phase timeline, the build log
(live while it runs, with "Show the whole log" when it was truncated), and on failure a link
to Diagnose plus Roll back and Redeploy.

![Deployment](assets/console/app-deployment.png)

With instant rollback, rolling back switches `current` and restarts behind the health gate in
seconds, and is not a job. On an application in a single folder (in place), rolling back
restores a backup and runs as a job. See [releases.md](releases.md).

**Settings** has a side navigation, a URL per subsection, and a save bar ("N unsaved changes ·
Discard · Save"):

| Subsection | What it has |
|---|---|
| General | Type, port, source, the branch it deploys from (pin or unpin it), commands, how deploys work, the service |
| Deploys | **Instant rollback**: what it gives you, what changes on disk, the migration plan and a rehearsal, then the migration; releases kept; the health check; blue/green |
| Deploy on push | The webhook as a guided setup: the public URL (`noust web expose-hooks`), the secret, the values to enter at the forge, the branch that deploys, and the last deliveries, ignored ones included |
| Builds | Whether it builds in the sandbox or as root and why; **Test in the sandbox**, then enable; the network profile; building as root with a reason |
| Resources | Memory (MB, at least 64), CPU (percent of one core) and tasks (at least 16), optionally restarting so they apply now |
| Previews | Pull request previews and their settings |
| Export | The application as a file to import elsewhere |
| Delete | Delete the application; removing its files and its certificate are separate choices, both off until you tick them, and you type its domain to confirm |

### Diagnose

![Diagnose](assets/console/app-diagnose.png)

Reached from the application's banner, or `/apps/<domain>/diagnose`. Correlates the unit's
state, the port it listens on, an HTTP probe straight to the application and one through nginx,
the last journal lines, nginx's error log for the domain, the certificate, the last deployment,
OOM kills in the last seven days and disk space. The most likely cause comes first, with each
check's evidence verbatim. Every probe only reads. The same report is `noust diagnose DOMAIN`.

### Databases

Two tabs: **Databases**, every database on every running engine with its size, application and
backups, and the ones created outside Noust to track; and **Engines**, each engine with its state,
version and end of life (install, start, stop, restart, uninstall). Database ports open beyond
the machine are flagged here.

One database's page has tabs: **Overview** (size, owner, application, backups, health),
**Data** (tables and rows, read-only, paged and filtered, with the columns' types; Redis keys;
editing one row needs sudo mode), **Query** (the SQL console: read-only by default, enforced
by the database server; history, saved statements, export, `EXPLAIN`; write mode needs sudo
mode), **Backups** (its policy with schedule, retention and destinations; restore as a new
database or over this one after a safety copy), **Users** (with owner, read-write and read-only
profiles, and password rotation), **Connect** (an SSH tunnel from your computer, never an open
port) and **Metrics**.

### Cron

Scheduled commands as systemd timers: create or edit with a preview of the next runs, run
now, enable, disable, and each job's recent runs with their output and exit status.

### Domains and certificates

Two tabs. **Certificates**: every certificate with its names, issuer and expiry; issue one
(method: automatic, nginx, Apache, webroot or standalone; extra names; `www`), renew those
due, renew one now, revoke, delete. **Sites**: every nginx or Apache site; create, enable,
disable, delete, and edit a site's configuration in a full-height editor with "Test" and "Test
and save": the web server tests it before it is installed.

![Domains and certificates](assets/console/domains.png)

### Backups

Three tabs: **Backups** (storage used per application, every backup: verify, restore, delete;
"New backup" with the same options as `noust backup create`), **Schedules** and
**Destinations** (remote places, their test and their key).

### Activity

Every job and every audited action on this server in one timeline, newest first, filterable
by kind, result and actor. A job opens its log.

### Server

Seven tabs, each with its URL:

| Tab | What it has |
|---|---|
| Overview | What needs attention, then the machine at a glance; fits one screen |
| Updates | Pending updates, security first; apply all or only security; refresh; "a reboot is due" and why; services on replaced libraries; automatic updates; the history of runs |
| Security | Hardening checks with their fix or exact steps, accepted risks; SSH (effective settings, keys, safe fixes that revert unless confirmed); the firewall against the ports that really answer; fail2ban |
| Storage | Real filesystems, what takes their space, clean-ups, swap |
| Services | Every systemd unit Noust manages ("Show all units" lists the rest, read-only); a service's page has its live journal, controls and a unit editor checked with `systemd-analyze verify` |
| Logs | The journal of any unit, with filters |
| System | Time and synchronisation, host name, the operating system and its end of life, processes, network, reboot and shut down (now or scheduled), and the resource monitor |

`/services` redirects to Server > Services.

### Settings

A side navigation with a page per section. On a central, each section says whose it is: the
selected server's, or this central's.

| Section | Scope | What it has |
|---|---|---|
| General | Server | Applications directory, web server, certificate email, backups, the server's name, and the console address `--open` links use |
| Notifications | Server | Channels (webhook, Slack, Discord, Telegram, email) in drawers, with a test each; which events notify; private destinations allowed |
| Integrations | Server | The GitHub App |
| About | Server | Version and updates, installation, CLI equivalents, links |
| Servers | Central | Every server, its reachability, version, ceiling and labels; **Add a server** walks through `noust fleet authorize` and the join code; test and remove |
| Accounts | Central | Accounts, roles, invitations, separation-of-duties exceptions (with `accounts.read`) |
| Security | Central | Your password, second factor and passkeys; sessions; the sign-in policy and the notice |
| API tokens | Central | Your tokens: issue (shown once), narrow, list and revoke |
| Approvals | Central | Four-eyes requests: yours, and those waiting for you to decide |
| Audit log | Central | The audit trail, its verification, destinations and reviews (with `audit.read`) |
| Compliance | Central | `noust ens check` and the evidence report |
| Central | Central | The role, the certificate and the seal |

On a server without a fleet, every section is the server's own. What a server's own accounts,
tokens and second factor are cannot be changed from a central; the page says how to manage them
on the server.

![Settings > Servers](assets/console/settings-servers.png)

Writing any setting needs sudo mode.

## Keyboard

| Keys | Does |
|---|---|
| `Ctrl K` / `Cmd K` | Open the command palette, from anywhere |
| `g` `o` | Go to Overview |
| `g` `a` | Go to Applications |
| `g` `b` | Go to Databases |
| `g` `f` | Go to the Fleet (a central) |
| `g` `s` | Go to Settings |
| `g` `d` | Go to the application's Deployments tab; outside an application, to Activity |
| `/` | Focus this page's search, or open the palette when it has none |
| `?` | Show the keyboard shortcuts |

Two-key sequences are typed one after the other within about a second. They do nothing while
you type in a field or while a dialog is open. Inside a log viewer, `Ctrl F` / `Cmd F`
focuses its search; `Enter` and `Shift Enter` step through matches.

The **command palette** searches pages (including each Settings section), applications with
their state, and actions: new application, change theme, keyboard shortcuts, sign out. Arrow
keys or `Ctrl N` / `Ctrl P` move, `Enter` runs, `Esc` closes.

## Sudo mode

Destructive and credential-changing actions (deleting anything, restoring a backup, revealing
or editing an `.env`, moving to instant rollback, changing limits, editing a unit or a site,
SQL in write mode, server changes, managing accounts, changing settings, issuing a token) ask
you to confirm it is you: a dialog titled "Confirm it's you" asks for your passkey, or your
password and a code from your authenticator app (for the access token, its code, or the token
itself when it has none). The confirmation covers the next 10 minutes, and the action you were
taking is retried once confirmed. Flows that already know they need it, such as deleting an
application, ask before their own confirmation dialog, so only one is ever on screen.

When approvals are on, an action that needs a second person becomes a request instead of
running: the console says so, it appears under Settings > Approvals for whoever may decide it,
and once approved you run it again from the same place, once.

## Live updates

The console holds one Server-Sent Events stream for the machine strip, charts, application
states, job progress and notifications, and opens a WebSocket for a log or a running job
while you look at it. If the connection drops it reconnects with backoff and refreshes
everything it shows. Log viewers render ANSI colours, search, follow the newest line (pausing
when you scroll up, with a "Jump to latest" button), wrap, copy and download.

## Themes

Light, dark, or the system's (the default), from the session menu or the command palette.
The choice is kept in the browser's local storage (`noust.theme`; a choice saved by WASM as
`wasm.theme` is carried over) and applies to every open tab.

## From the terminal

`--open` on `noust list`, `noust status`, `noust logs`, `noust db list`, `noust backup list` and
`noust monitor status` prints the matching console URL, and opens it when a display is
available. The address comes from `web.host` and `web.port` in the configuration (Settings >
General), which only affect these links: where the console listens is decided by the options
of `noust web start`.

## Accessibility

The console targets WCAG 2.2 level AA, and that is tested rather than declared:

- The end-to-end suite runs axe (WCAG 2.0, 2.1 and 2.2, levels A and AA) on every page, in
  both themes, and fails on any violation. It also fails on any Content Security Policy
  violation or console error.
- Colour contrast of the design tokens is checked by unit tests in both themes: 4.5:1 for
  text, 3:1 for interface elements.
- Everything works from the keyboard. Focus is always visible, a "Skip to content" link comes
  first, dialogs trap focus and return it, and after navigating focus moves to the new page's
  title.
- State is never colour alone: it always has a shape and a text label.
- Deploy transitions are announced politely and failures assertively through live regions.
  Log lines are never announced.
- Every chart has a text summary, works from the keyboard (arrow keys step through readings,
  announced in a live region), and its expanded view has a Data tab with the readings as a
  table.
- With `prefers-reduced-motion`, transitions are instant.

If something in the console is not usable with your assistive technology, please open an
issue on GitHub.
