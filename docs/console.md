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

**On a name of its own, over TLS (guided).** For a browser without a tunnel:

```bash
noust console expose --domain console.example.com --allow-ip 203.0.113.0/24 --allow-ip 198.51.100.7
noust console status                    # where it answers, and whether everything is still in place
noust console unexpose                  # removes exactly what expose made
```

`expose` writes an nginx site for the name (Noust's own, marked "Generated by Noust"), obtains
a Let's Encrypt certificate for it, makes the console believe nginx's forwarding headers (the
service gets `--trusted-proxy 127.0.0.1` and restarts), sets `web.public_url` to the name and
`web.allowed_hosts` to answer only it. nginx replaces `X-Forwarded-For` with the address it saw,
so nobody can pick the one the rate limit and the lockout are keyed on, and `--allow-ip` is
enforced there: everyone else is refused before the console sees them. Before it writes anything
it checks, and refuses with what to do:

- **a second factor on every account**: any account that can sign in with a password alone
  stops it (before any account exists, the access token's own second factor must be on);
- **the name**: not an application's (no application site is edited), not a site Noust did not
  write, and resolving to this server's addresses (`--skip-dns-check` for a server behind NAT);
- **the ENS profile**: the console stays on loopback unless `--force-ens` is given, confirmed,
  with `--allow-ip`; the override is audited (`console.expose` records `forced_ens`);
- **the console is a service** (`noust web enable`), and this is not a hub (a hub has no nginx;
  it serves its own TLS, see [CENTRAL.md](CENTRAL.md#the-consoles-certificate)).

There is no plain-HTTP form: until the certificate exists the site answers 404 to everything
but the challenge. A step that fails undoes what this run made, and `unexpose` removes only what
the record says Noust created: the site and the certificate when it made them, the trusted proxy
when it added it, and `web.public_url` and `web.allowed_hosts` while they still hold what it
wrote. The break-glass access token still signs in at the new address: keep it offline.

Over the API it is `GET`, `POST` (a job) and `DELETE` (a job) on `/api/console/exposure`, and
`POST /api/console/exposure/preview` to see the checks' answer without changing anything. Both
changes ask for sudo mode and `server.host_access`; the console restarts a few seconds after the
job ends.

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

**A server with no accounts** signs in with the access token (the **Access token** button, or the
link the installer printed, which opens the page on its field) and then opens one form that
creates the first administrator and signs in with it: a username and a password, with the name and
the email a click away. The console then offers to **Protect your account**, which can wait (**Set
up later**) unless a compliance framework asks for a second factor or the console is published.
Once an account exists, the access token is emergency access: it still signs in, and each use is
recorded. An invited person opens the invitation link, chooses a password and sets up a second
factor; where the server can send mail the invitation dialog also mails the link, and the sign-in
page offers "Forgot your password?", which mails a link that needs the authenticator as well. An
account sets up a second factor, an authenticator app or a passkey, at its first sign-in, before it
can do anything else, when a compliance framework asks for one or the console is published or
listens beyond loopback; elsewhere it is set up at **Account > Sign-in security** whenever the
person likes. When `auth.notice.text` is set, a notice of rights and
obligations is shown and must be accepted.

A session ends after 30 minutes without activity and 12 hours after sign-in (`auth.session.*`).
Five failed attempts lock the account for 15 minutes, and five from one address lock that
address; the page counts down.

## Layout

- **Sidebar**, in three groups: what runs (Overview, Projects, Applications, Databases, Domains,
  Backups and Cron), what happens (Logs and Activity) and the machine (Server). Projects is where
  teams, projects and environments live, and appears once they matter on the server. Settings is at
  the bottom. On a narrow screen the sidebar becomes a menu.
- **The scope selector**, at the head of the sidebar (in the top bar on a narrow screen), on a central
  or a server that has others: one server, **All servers** (the fleet) or **This central** (its own
  servers, security and tokens). The sidebar follows it: **All servers** and **This central** list the
  fleet's seven pages, Overview, Servers, Applications, Certificates, Backups, Updates and Activity.
  `/n/<server>/...` keeps the choice across a reload, the last server is remembered, and every page for
  one server shows its name above the title. A lone server draws no selector.
- **Top bar**: the machine strip (host name, uptime, load, CPU, memory, disk, units running and
  failed), the command palette, the **inbox** and the session menu (account, sign-in security, theme,
  language, keyboard shortcuts, sign out). The inbox is a bell: **Needs you** lists what is wrong while
  it is wrong (a failed application, a certificate about to expire, an alert that fires, a probe that is
  down, a request waiting for your decision, a Noust or a recipe with a newer version), each row with
  one button that goes to it, and **Recent** keeps the last 30 things the server said while this tab
  was open. The history is Activity.
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
When the charts' history is not being recorded, it says why and what to run. The Overview has a
second view, the [Map](#map). A server with nothing deployed shows one card instead, **Deploy your
first app**: **Try a demo** (a tiny page on a temporary address), **Use a repository**, four popular
recipes and **All recipes**, and under it **Then make it safe to leave alone** (backups, a copy off
the server, notifications and, while the account is not protected, **Protect your account**). On a
hub, the home page is the fleet summary.

### Map

`/map` (the Overview's second tab, or the palette: "Open the map of this server") draws how this server fits
together: the names that reach each application, the applications with their state, the databases they use,
the scheduled work they run and the data they keep (persistent paths and mounted paths), in fixed columns
from left to right, with the lines that connect them. It is **read from the server on every visit** and
carries no coordinates, so there is no canvas to arrange, nothing to save and nothing that moves when you
look again: the same server is the same picture. Pointing at or focusing an element lights its whole way,
as in a site's diagram; the same connections are a table with a link to each thing, and on a phone each
column is a list. A team member's map has its team's applications and databases and nothing else, and
a project is drawn apart from another when there is more than one to tell apart. The same relations
are `noust map` (and `noust map --json`, the document `GET /api/map` answers).

### Charts

The charts drawn from the [metrics history](MONITOR.md#metrics-history) (the Overview's machine
charts, an application's Metrics tab and a database's Overview and Metrics tabs) behave the same
way everywhere.

- **A group of charts** shares one crosshair: pointing at one marks the same moment on the others,
  but only the chart under the pointer says the moment and its readings, and the rest keep the
  newest values in their row. A click no longer freezes anything: until 3.2 it froze that chart and,
  through the group, every other one until the next click. The keyboard still steps through the
  readings, and Escape hides the card.
- **Expanded**, a chart has the range selector and the zoom. Dragging across it selects a stretch
  of time, drawn as a translucent tint with solid edges so the readings under it stay visible, and
  zooms into it: the stretch is read again at a finer step.
- **Investigate this stretch** is a button of the expanded chart. It acts on the stretch on screen
  (the one you zoomed to, or the whole window) and opens a panel, "What happened in this stretch",
  with what Noust knows about it in one place, up to 31 days:
  - **Busiest processes**: the five processes with the most CPU and the five with the most memory
    in each minute, with the service, application or container each belongs to, the account, and
    (with `secrets.reveal`) its command line. A row selects that minute and lists its processes below; when the stretch is long,
    only its busiest minutes are listed, and the panel says so. This is what answers "why this
    peak". The monitor takes these samples once a minute while it runs, so there are none from
    before it started: the panel says "No process samples before ..." with the time they begin.
  - **Events**, oldest first, filtered by source, level and service: the system journal from
    warnings up, the audit log (commands, console and central actions, sign-ins, with who), what
    the monitor saw (processes over a threshold, units that failed or recovered, server boots),
    and the deployments and jobs that ran.
  - **Deployments and jobs**, each opening its page.

  On an application's Metrics tab the stretch is narrowed to that application: its journal (every
  priority), the audit events that name it, its deployments and jobs, and its processes. On a
  database's charts and the Overview it covers the whole server. A source you may not read is
  named with the permission it needs, never left out silently: the journal needs `secrets.reveal`,
  the audit log `audit.read`. A source that failed shows the system's own words. On a central,
  each server answers for what it saw. The same data is `GET /api/timeline`, see [api.md](api.md).

### Fleet

A [central's](CENTRAL.md) own pages, hidden on a plain server: choose **All servers** in the scope
selector and the sidebar lists them. **Overview** (the fleet's key figures and what needs attention
on every server, worst first), **Servers** (reachability, version, load, memory, disk, labels),
**Applications**, **Certificates**, **Backups**, **Updates** and **Activity**, each row with its
server and a link to its page there. The
fleet's Overview ends with **Compare servers**: one measurement of every server (CPU, memory, swap, disk,
load, network) over the same window, the busiest first, from each server's own history; a server
that did not answer is a gap, said so. A server
that did not answer, is too old for a view, or refused you is one row with its state and its
own words; the rest of the page is unaffected.

**Bulk actions** (renew certificates, back up, verify backups, update or restart applications,
update Noust, apply system updates) are chosen from a list of servers or by label, show their
plan first (which servers, in which batches, what is skipped and why), and run as a fleet job
with each server's outcome and a button to retry the servers that failed. **Add a server**
is on **Servers**, and the selector and the palette lead to it.

On a fleet job's page, **Details** on a server's row opens what happened there:

- **A failure first**, with the server's own words verbatim and the suggested fix above them.
- **Its state**, how long it really took (or has been running), its batch, and what the job
  returned in words: for a system update, how many packages and which, whether a reboot is due
  (nothing is rebooted) and which services still run old libraries; for Noust's update, the version
  it went from and to; for a certificate renewal, each certificate renewed and its new expiry. A
  server older than 3.2 does not say which certificates, so nothing is guessed.
- **The job's log on that server**, in the log viewer (search, copy, download, follow the end):
  live while it runs, over the job WebSocket the central relays through the tunnel, and whole once
  it ended. A server that ran several jobs for the action has one tab per job.
- **Each application**, only for the actions that work per application (update, restart, back up,
  verify backups), with its domain, its result and a link to its deployment or its backup.
- **Open on the server**, a link to that job in the server's own Activity.

An operating system update that upgrades the `noust` package restarts the node's console in the
middle. The plan says so beforehand ("Also updates Noust: its console restarts, and the job waits for it"; Noust is installed last), and the
central waits for the node to come back and reads the result instead of counting a failure: the
node's console follows a job that runs in a transient systemd unit again after it restarts (see
[Live updates](#live-updates)), so the job ends as the unit did.

**On several servers** is the second tab of Applications (on **All servers**), for an application that
runs on more than one server
([CENTRAL.md](CENTRAL.md#an-application-on-several-servers)): each application is a card with its servers
side by side - the commit each serves, when, and whether it is current, differs, is missing or did not
answer - and a line that says whether the fleet is in sync or which commit most servers run. Its menu
**rolls a commit, a tag or an image tag out** (the plan first: who goes first and alone, the batches, what
is skipped and why; then the fleet job it opens), **puts the application on a server that lacks it**
(from one that has it, optionally under another domain), **writes the servers for the load balancer**
(nginx, a list or JSON, with a port) and edits or stops tracking the placement; stopping touches no
server. A server's menu on **Servers** adds **Connection options** (the wait for the connection and a
bastion, tried before it is kept, with the line to authorize on the bastion), **Export its inventory** and
**Apply an inventory** (to move a server to another central) and **Link to a cloud machine**.

![Fleet](assets/console/fleet.png)

The scope selector switches every other page to one server, and every action taken there
(including a destructive one, still behind "Confirm it's you") reaches that server's own API
through the central's tunnel. What the server's ceiling for the central does not allow is
greyed out with the reason. A hub (`central.role = hub`) deploys nothing of its own; switched to
a server, Applications (and every other page) is that server's own:

![A node's applications](assets/console/node-apps.png)

### Applications

![Applications](assets/console/apps.png)

Every application with its state, type, port, last deploy, CPU and memory, filterable by
state, type and, once any app carries one, **tag**. Narrowed to one tag, the page offers **Update
tagged apps**: each app that carries it pulls its source and builds a new release, as Update does,
and one with nothing new is skipped unless you tick **Rebuild even when there is nothing new**. A
row's menu also has **Promote to another environment** (the release that serves goes to the same app
in another environment of its project, without being built again) and **Clone application** (a new
app with this one's settings and a domain you give it, in this or another environment; when the app
has a database you choose between one of its own and sharing it). See [teams.md](teams.md).

On a phone, rows become cards with their actions always visible.

Each row has a checkbox. With some chosen, **Act on N selected** opens the plan of an action
(restart, update, stop or start): what will be done to each application, and which are left out and
why (a static site has no process to restart, an application already stopped is not stopped again,
one being deployed is not restarted). **Restart N applications** then does it one application at a
time, in the order chosen, with the server's own words for any that fails and **Retry the failed**
for those alone. Each is the call the application's page makes, so sudo mode, the audit trail and
the update queue are the same.

**New application** (`/apps/new`) asks for what it needs and nothing else. In **Source** you give a
Git URL, a path on the server or a Docker image, or choose **From GitHub**; what you give is read at
once, and a line under the field says what was found, with **Change**: the detected type (editable),
the commands, the port and the variables declared in `.env.example` as a form, with secrets flagged
(`Detected: Next.js with npm. Port 3000. 2 variables to fill in.`). **More ways** are Compose, a
recipe, an import (an export file, or an application read from a Coolify or Dokploy server with a
read-only token) and a site that already runs. Then **Address** (a name of your own, whose DNS the
console checks, or, on a server with a public address, a temporary one such as
`shop.203-0-113-10.sslip.io`, which needs none), **Configuration**, **Variables**, **Database**
(optional: one created and linked before the first build, its connection string in the app's
environment) and **Deploy**. The stepper lists only the steps that ask something, and the summary
before deploying says what was detected, the address and the variables, each with **Change**. The
deploy streams the build log until the application answers, and ends on one card: the address with
copy and open, whether HTTPS is ready, and what to do next. On a fleet there is also a **Server** step,
or switch to the server first. A repository the console cannot clone shows git's own words and this
server's public key, to add at the code host. Leaving with something chosen asks first.

The Source step also offers **A running stack**, for a Docker Compose stack that already runs on
this server and that Noust did not deploy (one started by its own `deploy.sh`, say). You give the
domain it serves and the directory it runs from, and under **More options** its compose file, the
site file that serves the domain, the repository updates fetch from and the branch. **Preview**
reads the project from the running containers' labels, finds the site, and shows the output of
`docker compose up --dry-run` verbatim: it must say nothing would be recreated. **Adopt** then
registers the stack without cloning, cleaning, rebuilding or restarting anything, and installs its
service without starting it. If the rehearsal lists changes, the adoption is refused with
Compose's own output, unless you tick **Adopt anyway, recreating what Compose lists** after reading
it. Adopting needs sudo mode. See [compose.md](compose.md#adopting-a-running-stack).

![New application](assets/console/apps-new.png)

Besides a repository, the Source step offers **A Docker image**: an image from a registry runs as
a container application, with nothing to inspect (and no source: the server refuses one given
with it). A repository with only a Dockerfile is proposed as a container application too, and
Configuration then asks how its image is made: a Dockerfile (its path, the context, the stage,
build arguments and build secrets) or Railpack, the port the process listens on, its volumes, how it
restarts and, for an image, a private registry's account. A build secret's value and a registry
password are typed into masked fields, sent once and never shown again; the last step names how the
image is made and never a secret. A container's releases are images, so the layout and the folders
kept in `shared/` are not offered, and neither is a database: it cannot reach the host's. See
[containers.md](containers.md).

### Recipes

The recipes this release ships (`noust recipe list`), reached from **Applications** and kept at
`/catalog` (so are the installed ones, `/catalog/installed`), each as a tile with a **Popular** row
before any filter: its logo (an image the server
serves, within the strict policy), name and slogan, its category from a closed list, tags, the
version it pins, the day the recipe last changed, links to its site and documentation, **Runs in
Docker** for a Compose recipe and what a native one needs (a database Noust sets up, a PHP-FPM). A
recipe this release cannot deploy is listed with the server's reason and no button. Search and the
category, tag and **Runs** (in Docker or natively) filters are the server's own search, kept in the
address; the list pages at 20, 50, 100 or all recipes. **Use** opens the wizard on that recipe's
one step: where it answers and the variables it cannot run without, each with what it is for and
the default an empty field keeps (a secret is masked), and Deploy waits for the required ones and
leads to the field. **Details** opens the recipe's own page: what it runs (image, tag and digest),
what it needs, every variable and where its value comes from, the ports it opens on purpose, what it
will tell you after the install and what each version changed. The install is a job, its output a
click away while it prints.

A recipe's page also lists what installing it needs from this server (memory, disk, the ports it
opens, the runtime and the database engines), each met or missing, and the wizard shows the same before
anything is made and asks whether to back the new application up every day. **Add a recipe** checks a
recipe you paste against the catalog's schema and its lint and keeps it on this server, apart from the
ones Noust ships; a recipe of yours says so on its card. **Catalog package** shows the signed package that
grows the catalog between releases (who signed it, its serial, what is in it) and fetches or removes it.
The new-application wizard also takes **Compose**: it is read first, without running anything,
and what refuses it or is worth knowing is shown before it is deployed. See [recipes.md](recipes.md).

**Installed** lists what was installed from a recipe, the version it runs against the catalog's
and what changed. **Update** is the one update (a copy first, the startup check, the previous version
back when it fails) queued as a job; a variable the new version asks for and the application lacks is
asked for, the ones it has keep their value. An application whose recipe left the catalog says so,
in the server's words. See [recipes.md](recipes.md).

### One application

The header shows the domain, state, type, port and a link to the live site. A banner between the
header and the tabs appears while a job runs on it, when its last deploy failed or when it is
down, with a link to **Diagnose**. Tabs:

| Tab | What it has |
|---|---|
| Overview | What is live now and what changed, the last deployments (a failed one says which version kept serving), the application's health (uptime, certificate, CPU and memory, with the way to change its limits), its previews, and where it answers and whether a push deploys it |
| Deployments | The application's [queue](#the-deploy-queue-and-cancelling) when something builds or waits for it; the deployment history in one list: what serves is marked **Live**, a failed deploy says which version kept serving, a deploy that was cancelled is drawn as **Cancelled** and never as a failure, search and filters (state, branch, who started it) are in the address, and each row opens its page or acts from its menu (**View log**, **Roll back to this**, **Retry**, **Rebuild this commit**, **Copy commit**); a build made with **Build without serving** waits at the top with **Go live**; an application kept in a single folder lists its backups under the list |
| Logs | The unit's journal, live; for a Docker Compose stack or a container application, what the containers printed (all services or one, read again every few seconds), with the unit's journal as one choice |
| Metrics | CPU and memory (requests and errors for a static site) over an hour, a day, a week or a month, against its limits, with deploys marked; or why there is nothing to show. A Compose stack of two containers or more adds CPU, memory, disk and network **by container** (the busiest three of each), and a site whose access log is read adds **who asks for what**: requests and errors by the hour, data served, and the pages, networks, status codes and referrers that appear most |
| Environment | The `.env`, redacted; revealing a secret needs sudo mode. A row opens on press, and a hidden value is never read to be edited: it is kept, or replaced (**Replace value**). A change shows in its row, the old value struck beside the new one, and the bar at the foot writes only what changed: **Save and restart**, or **Save only** from its menu, which leaves the change waiting as "not applied". A pasted file, or more than six changes, is looked over in full first. A value made of references (`{{project.NAME}}`, `{{secret:vault/...}}`) is shown as written, not as what it resolved to, and **Resolve references** reads them again without a deploy. **Given to** (build and runtime, build only, runtime only) is in each row's menu and in the add dialog, and saved with the values. **Given by Noust** lists `NOUST_URL`, `NOUST_BRANCH` and the rest, read-only. See [variables.md](variables.md) |
| Database | The databases it uses, creating and linking one, and its connection string |
| Domains | Primary, aliases and redirects, with a DNS check per name; add a name as an alias or a redirect (Docker Compose stacks that publish a port and monorepos included; see [domains.md](domains.md)) |
| Settings | One page per subsection, below |

![Application](assets/console/app-overview.png)

**Changes not applied yet.** When an application has been set to something it does not run
with yet (a variable, a limit, a command, the sandbox, a mounted file), a chip on the header's
state line says how many (**2 not applied**), and the Environment tab counts its own. The chip
lists each change by area with what it needs, a restart, a build or the next deploy; the values
of secret settings are never shown, only that they changed, and **Restart now** (or **Build
again now**) applies them when that is all they need. **Deploys paused** and **Deploys frozen**
are said the same way, and lead to where they are resumed.

**Update** is a split button: its arrow holds **Update without cache** and, on an application
with instant rollback, **Build without serving**. **Roll back** is one question wherever it is
asked from (the header, a row of the history, a deployment's page, Diagnose): which version,
what goes back, what stays as it is, and whether automatic deploys will pause.
See [runtime.md](runtime.md#changes-not-applied-yet).

**Files** is in **More actions**, for whoever may open a terminal: the application's files a
folder at a time (the folder is in the address), as the account the application runs as, with
edit, upload, download, new folder, rename and delete. Environment files are the Environment's
and are not opened here. See [runtime.md](runtime.md#files).

Each deployment has its own page: commit, trigger, duration, a phase timeline, the build log
(live while it runs, with "Show the whole log" when it was truncated, and filters by level and
text, a switch for the times, and **Copy to share**, which masks anything that looks like a
credential), and on failure a link
to Diagnose plus Roll back and Redeploy.

A deployment that ran [deploy hooks](releases.md#hooks-and-schema-aware-rollback) lists them
under **Deploy hooks**, in the order they ran, before the new version served and after it did.
Each shows how it ended (succeeded, exited with a code, ran out of time, did not run), how long it
took, the service it ran in on a Compose stack, and the end of its output verbatim; Prisma's
automatic migration is listed the same way. The section also says whether the deploy changed the
database schema, and the list of deployments marks such a deploy with a **Database changed**
badge. When a hook that runs after serving failed, the page says **Deployed with warnings**: the
new version serves, nothing was undone, and the hook's output is shown (the `deploy_hook_failed`
notification says the same).

![Deployment](assets/console/app-deployment.png)

With instant rollback, rolling back switches `current` and restarts behind the health gate in
seconds, and is not a job. On an application in a single folder (in place), rolling back
restores a backup and runs as a job. See [releases.md](releases.md).

**Going back past a change to the database.** Noust puts code back, never a database. When later
deployments changed the schema, every way back asks first: rolling back to a deployment,
activating a release, the Roll back dialog's backups, and restoring a backup's files. Before you
press the button, the deployment's dialog says which deployments changed the schema after it. If
you go ahead, **Going back passes a change to the database** lists those deployments with the
migrations each one ran, names the backup taken before the first of them and the
`noust backup restore` command that puts the database back (for a Compose stack,
`--databases-only`), and **Go back anyway** repeats the request confirmed. A failed health gate
still puts back what served before, whatever the attempt changed. See
[releases.md](releases.md#hooks-and-schema-aware-rollback).

**Settings** has a side navigation, a URL per subsection, and a save bar ("N unsaved changes ·
Discard · Save"):

| Subsection | What it has |
|---|---|
| General | Type, port, source, **App tags** (the labels that group it, edited from here), the branch it deploys from (pin or unpin it), the tags it deploys (**Follow tags...**: a pattern such as `v*`; see [compose.md](compose.md#deploying-by-tag)), commands, how deploys work, the service and the account it runs as (**Give it its own account...**: its service, files and `.env` move to an account of its own and the app restarts once; if it does not answer afterwards everything is put back exactly as it was). On a Compose stack that publishes no port but still has one recorded, **A worker reported as down** offers **Record it as a worker...**, which clears the port and judges the stack by its containers |
| Source | How the server signs in to the repository: its **deploy key** (an SSH key for that repository alone, made on a click or imported, its public half shown to add at the code host with a copy button and a link to the host's page, **Test the connection** as a deploy would, **Replace the key** without a moment when the repository has none that works, and **Remove**) and, for a repository cloned over https, an **HTTPS credential** (a user and a token kept as a secret, never shown again). Writing a secret asks for sudo mode first. See [api.md](api.md#deploy-keys) |
| Container | A container application only: how its image is made (a Dockerfile, Railpack or a registry image), the Dockerfile, context and stage, build arguments, build secrets (their ids, never their values; one can be removed), the port, the volumes, the restart policy the registry account of the image itself (its password is write-only: it is never shown and an empty field keeps it) and the **registry accounts** it pulls with, chosen among the ones Settings > Integrations holds. A change is stored at once and takes effect with the next update |
| Build | The install, build and start commands, the base directory, the publish directory and the runtime of a type that has a build block (not a Compose stack, a monorepo or a container), where they come from (this application's own settings, the repository's `noust.yaml` or the type) and that the application's own settings win whole; saving asks for sudo mode, and the application's own settings can be removed. Below them, the **System packages** the host needs (edited in a drawer, installed with a button), the **Build cache** and the **Process types**; and the **Build sandbox** card: whether it builds in the sandbox or as root and why, **Test a sandboxed build**, then turn it on (the trial Noust runs by itself before the next update of an app that still builds as root is recorded here like one you ran), the network profile, and building as root with a reason. A Compose stack or a container shows the sandbox and says why it has no commands here. See [build.md](build.md) |
| Releases and rollback | **Instant rollback**: what it gives you, what changes on disk, the migration plan and a rehearsal, then the migration; releases kept; the **startup check**; and **Zero-downtime deploys** (blue/green, which new applications that can run as two copies start with by default; on a Docker Compose stack, the relay, which says what to change first when the stack or its site cannot use it). Instant rollback and zero-downtime deploys say whether they are on in the header of their card |
| Automation | What deploys the app without a person asking and what holds, plans and protects its updates, as rows with no save bar: **Automatic deploys** (a switch; turning it off pauses what a push, a tag or a schedule deploys, with a reason, and a second switch decides whether a rollback pauses by itself), whether an update is refused when its **backup** fails, on a Compose stack the **copy of the databases before an update**, **Freeze deploys** for an application, **Scheduled updates** (when it updates itself, with its backup first, and how the last run ended; set in a drawer) and the **deploy limits** (how many deploys are kept, the time and the signal a unit stops with; also a drawer). A freeze or a pause is also said on the Deployments tab. See [delivery.md](delivery.md) |
| Compose | A Docker Compose stack only: its options, the networks it joins and the **registry accounts** it pulls and builds with, saved together from one save bar. See [compose.md](compose.md) |
| Deploy on push | The webhook as a guided setup for GitHub, GitLab, Gitea and Bitbucket Cloud, in three steps (the public address, the secret, what to enter at the forge) of which the one the setup is at is open and the ones done can be read again: the public URL (`noust web expose-hooks`, or the console's own address on a network the forge shares), the secret, the values to enter at the forge, the branch that deploys, and the last pushes received, ignored ones included. When this server's GitHub App already reaches the repository the first step says no webhook is needed. An app that follows tags says so here: releases and tags that match deploy, pushes to a branch do not, and a delivery for an older or unmatched tag is listed as ignored |
| Deploy hooks | The commands each deploy runs before the new version serves and after it does, where they come from (**From the repository**: the `noust.yaml` of the running code; or **From the operator**, which replaces it whole), each with its service, directory, time limit and whether it changes the schema. The operator's hooks are written here as YAML in the shape of a `noust.yaml`; saving asks for sudo mode and may wait for a second person's approval. An invalid `noust.yaml` is shown with the reason, and the next deploy fails with it until it is fixed or replaced by the operator's hooks. A static site has no hooks |
| Resources | Memory (MB, at least 64), CPU (percent of one core) and tasks (at least 16), optionally restarting so they apply now |
| Processes | The copies of the web process (each new one answers the startup check before it takes traffic) and the processes that run beside it (workers, schedulers): add, change, scale and remove. See [runtime.md](runtime.md#processes) |
| Service security | The systemd sandbox of the service (`off`, `default`, `strict`), the folders it may write to, how exposed systemd finds it, applied behind the health gate. See [runtime.md](runtime.md#service-security) |
| Mounts | Configuration files kept for the application and linked into every release (their text is editable here, and needs the permission that reveals secrets), and server folders bound into a container application. See [runtime.md](runtime.md#mounted-files-and-paths) |
| Previews | Pull request previews and their settings: where they answer, how many and for how long, **which variables a preview is given** (only the ones you choose, the default, or all of the app's) and **which data** (a database of its own, the default, or the app's), each choice that exposes something with one line saying what. Each preview has its **Logs** (the last build and the service), **Update**, **Stop** or **Start**, and **Remove**; **New preview** makes one from an open pull request (when the GitHub App reaches the repository), a branch or a tag; the variables defined for previews alone are listed below them. A Docker Compose application has previews too. The settings also give **how a preview is named** (a template, with the name it gives drawn beside it), whether to **keep search engines out**, what to do with **pull requests from forks** (refuse them, or wait for your approval of each commit, which shows on the preview as **Approve** and **Reject**) and a **copy of the database** as a third choice of data. See [previews.md](previews.md) |
| Access | Who may open the site: a password, named users, the addresses allowed, a private network only, how the visitor's address is read, a sign-in service in front of the site, and the behaviour of a prefix of the site. Saved, tested by the web server and reloaded together |
| Protection | What the site limits and sends back: request rate, response headers, HTTP/3, the web application firewall and client certificates. Saved, tested by the web server and reloaded together |
| Export | The application as a file to import elsewhere, and its declaration (`noust.yaml`): **Download the declaration**, or **Apply a declaration**, which can start from what runs now, shows the plan and then every block that differs line by line with a way to put a block back, and applies only what was checked. See [declarative.md](declarative.md#reviewing-a-set-of-changes) |
| Delete | Delete the application; removing its files and its certificate are separate choices, both off until you tick them, and you type its domain to confirm |

### The deploy queue and cancelling

Builds of different applications run side by side, up to the server's limit (`deploy.max_concurrent_builds`);
one application builds one thing at a time, so what is asked meanwhile waits its turn and nothing is lost
(see [releases.md](releases.md#the-deploy-queue)). The queue is on **Activity** when something builds or
waits, and on an application's **Deployments** tab for what is its own:

- every job is a row with its state (queued, in progress, stopping), its **place in line** ("Next in line",
  "Place 3 in line") and, for one that waits, **what it waits for** in the queue's own words, set in mono: the
  update of the same application that is running, a free build slot, a job that changes the whole server;
- a request for an update that is already waiting is **merged** into it (the last push wins, nothing builds
  twice): the row says "1 request merged into this one", and pressing **Update** says it joined the waiting
  one;
- the server's limits are in the panel's header ("1 of 2 builds running", "3 of 25 waiting"); a request the
  queue has no room for is refused, and the console says so with the seconds the server asked to wait
  ("Try again in 30 seconds, or cancel what no longer needs to wait"), and the server's own sentence under it;
- **Cancel** is on each row and on the job in hand under an application's header. A job that waits is taken
  out of the line, after one question. A deploy or an update that is building is **stopped**: the question says
  that the release it was making is removed, what serves now is not touched and the next request starts. Once
  its release starts to be activated, or when the application builds in the folder that serves (a single
  folder, a Compose stack), or for a job that is halfway through changing the system, the button is disabled
  and the reason is written beside it. A stop that was asked shows **Stopping** until the build has wound down.
  The same is `noust deploy cancel`.

A deploy that was cancelled is recorded as cancelled, with who cancelled it: the history, the deployment's page
and the overview never draw it as a failure and never raise it.

### Diagnose

![Diagnose](assets/console/app-diagnose.png)

Reached from the application's banner, or `/apps/<domain>/diagnose`. Correlates the unit's
state, the port it listens on, an HTTP probe straight to the application and one through nginx,
the last journal lines, nginx's error log for the domain, the certificate, the last deployment,
OOM kills in the last seven days and disk space. The most likely cause comes first, with each
check's evidence verbatim. Every probe only reads. The same report is `noust diagnose DOMAIN`. For an
application that restarts in place on every update and could update without a cut, it also says so, above the
checks, with a link to the setting that turns it on.
A Docker Compose stack that publishes no port (a worker) has no port or site to probe: it is
judged by its containers, and the evidence is their state and the last lines of `docker compose
logs`; when a port is still recorded for it, the report says so and names `noust app headless`.

### Terminal

Off the tab strip, under **More actions > Terminal** (`/apps/<domain>/terminal`): a shell as the
account the application runs as, in its sandbox and with its environment (a stack's, in its
container), and below it **Run a command once**, which splits what you type into the program and
its arguments, shows the list it will run, and shows the output as the job's log. The server's own
root shell is **Server > More actions > Open a terminal**, off until an administrator turns it
on. Both need sudo mode, are limited in time and recorded in the audit trail. The page is drawn on
a canvas, with a text view for screen readers; Escape, then Tab, leaves it with the keyboard. See
[Terminal and one-off commands](terminal.md).

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
profiles, and password rotation), **Connect** (an SSH tunnel from your computer, and **Public
access**, below) and **Metrics**.

**Engines** also lists the **instances Noust runs**: another version of an engine, or one the server
lacks, as a container from the engine's image pinned by digest, on 127.0.0.1 and held to a memory limit
(**New instance**, with the plan before the sudo-mode job; for PostgreSQL the **Image** chooses the
engine's own or a variant with PostGIS or pgvector installed). An instance's menu moves it to the newest
image of its version, **Move to a newer version** (every database goes to a new instance of the newer
major version, with the plan first and a way back listed under **Moves to a newer version**), **TLS**
(the mode, the certificate and the authority applications trust, with the applications that lose their
connection while the engine restarts shown before it is applied; renew the certificate), **Limits and
health** (memory, CPUs, swap and the health probe's interval, timeout and failures, with what Docker says
of the engine now) and removes it. A database's menu has **Move to another engine**, which dumps it, makes
its accounts on the target, loads it and points each application that uses it at the target behind its
health gate, leaving the source untouched. On a PostgreSQL database's **Overview**, **Extensions** lists
the ones loaded and loads or unloads one from what the server offers (one that is not trusted is the whole
server's to load). Linking an existing database offers **Connection security** (encrypt, check the
authority, check the authority and the name); the application's Database tab says how each is reached.
See [databases.md](databases.md).

A database is **private** until you open it: **Public access** on **Connect** makes it reachable
from your own computer on a port you choose, from the addresses you list, with TLS required where
the engine can (a PostgreSQL account is refused when its client does not use TLS), through a
proxy socket so the engine itself stays on loopback. The first step names the port, who may reach
it and what the account can do (owner, read and write, read only); the second says what Noust will
open, the firewall rule, and every warning the server has, in its own words, and asks you to tick
that you accept them before **Make it public** (sudo mode). The account's password is shown once
(**Show the connection** shows it again after confirming it is you), and **Make it private**
removes exactly what opening made. A new database for an application (**Application > Database**)
asks which engine and which version: the one that runs, or, for an engine that is not installed,
the versions Noust can install first, which it does only when you tick it.

**Backups** has two scopes: a policy for one database, and a policy for **every database of an
engine**, the ones created later included, with the databases, schemas and tables (collections in
MongoDB) it leaves out. A database with a policy of its own keeps it. **Import a dump** (on the
list, and on a database's Backups tab) uploads a file from your browser in chunks of 8 MiB that
survive a dropped connection (it waits, tells you, and goes on from what the server holds; **Try
now** does not wait), up to the size the server allows (`databases.import_max_bytes`), reads what
the file is from its bytes, offers to drop and recreate the database or keep the owners the dump
names, asks you to type the database's name before it replaces anything, and follows the import
as a job. A file over the limit is imported from a terminal by its path (`noust db import`).

### Cron

Scheduled commands as systemd timers: create or edit with a preview of the next runs, run
now, enable, disable, and each job's recent runs with their output and exit status. A job can be
given the longest it may run and, for an application that is a Compose stack, the service of the
stack it runs in (see [compose.md](compose.md#scheduled-tasks-of-a-stack)).

### Domains

Two tabs. **Certificates**: every certificate with its names, issuer and expiry; issue one
(method: automatic, nginx, Apache, webroot or standalone; extra names; `www`), renew those
due, renew one now, revoke, delete. **Sites**: every nginx or Apache site; create, enable,
disable, delete, and edit a site's configuration in a full-height editor with "Test" and "Test
and save": the web server tests it before it is installed.

The list shows every site file the web server has, the ones Noust wrote and the ones you wrote:
**Written by** says "Noust" or "By hand", and **Application** names the application whose site it
is, or says it is not an application's. A site written by hand is never rewritten by a deploy
(see [domains.md](domains.md#operator-sites)).

Three more tabs are shown to whoever may see the server: **DNS** (the accounts Noust holds at DNS
providers and the records it manages in them), **Tunnels** (Cloudflare Tunnels and the names each
carries) and **Ports** (the TCP and UDP ports nginx publishes). The Certificates tab can **upload**
a certificate and its key, opens the **certificate authority** settings and the **private authority**
(the server's own, for names that only resolve inside a network: made, trusted on this server, how to
trust it on each kind of device, the certificates it signed), from More actions; see
[certificates.md](certificates.md) and
[network.md](network.md). An application's **Domains** tab also chooses the spelling that is served
(`www` or the bare name), sends an alias to another port, shows and makes the DNS records of its
names, and chooses where its certificate comes from; its **Settings, Access** holds the password, the address lists, the private network,
the sign-in service and the routes of its site, and **Settings, Protection** the limits, the
response headers, HTTP/3, the firewall and the client certificates.

![Domains](assets/console/domains.png)

#### A site as text, structure and diagram

A site's page (`/domains/sites/<site>`) shows its configuration in three views, chosen with the
**View** control and kept in the URL (`?view=structure`, `?view=diagram`; the Text view has none).
Only nginx and Apache sites have all three. The views share **one draft**, the text: what you
change in one appears in the others, the bar at the foot (changed lines, **Discard**, **Test**,
**Test and save**) is the same, and leaving the page with changes asks first. There is **one way
to save**: the same `PUT /api/sites/{domain}/config` whichever view made the change, which needs
sudo mode, may need a second person's approval, and tests the whole text with the web server before
it writes anything. A site whose text cannot be read (a draft with a syntax error) shows the
analyzer's own message with its line and column and a **Go to text** button, never half a
structure.

- **Text** is the editor, as before.
- **Structure** shows the site as what it is:
  - a card per `server`: where it listens as chips (`443 · TLS · HTTP/2`, with `default_server` where it is one), its names, its
    certificate with the days left, its maximum body size, and a note when it answers every
    request itself with a `return`;
  - the **locations in the order the web server tries them**, not the order of the file (exact
    matches first, then the longest prefix, regular expressions in file order unless a `^~` prefix
    stops them). Each has its kind of match, its path, and where it goes: an upstream (a chip that
    scrolls to that upstream's card), a proxied address, the files of a directory, a redirect,
    FastCGI. The settings used most are fields in the row (read timeout, maximum body size, rate
    limit, WebSocket, buffering), and **More directives** opens every other directive of the
    location as written, editable;
  - **Upstreams** with their servers, how many locations use each, and "Defined in ..., read only
    here" for one an included file declares; **Other directives** (maps, zones, anything the
    structure does not model) and **Included files**, as written: nothing is hidden;
  - the comments of the file as notes next to the element they belong to;
  - **Add location** (**Proxy**, **Static files** or **Redirect**), and from each location's menu
    **Duplicate**, **Move up in the file**, **Move down in the file** and **Remove**. Moving is only
    ever what you ask for: the structure never reorders the file to tidy it.

  A text field applies when you press Enter or leave it, not on every key, and Escape puts back
  what the file says. Each change is an operation sent to the backend
  (`POST /api/sites/{domain}/config/edit`), which changes only the bytes of the element concerned
  and keeps its indentation; the rest of the file stays identical, comments and blank lines
  included. The answer is the new draft, and the changed elements are marked **Changed**. Nothing
  is written to disk until **Test and save**.
- **Diagram** is read only. It draws how a request travels, in columns: **Ports**, **Names**,
  **Locations**, **Destinations** and **Backends**. Every element has a shape and a text label;
  colour only says whether a backend answers (responds, not responding, unknown), always with its
  glyph and word, and a backend says who holds its port: a Noust application, a service of a
  Compose project, a container, a systemd unit or another process. Dashes move along each
  connection in the direction of the traffic, faster where the connection is encrypted, and a
  WebSocket is drawn as a double line; with `prefers-reduced-motion` they stand still. Pointing at
  or focusing an element lights its whole path, and pressing it keeps it lit. Under the drawing
  are a written summary (how many connections, which elements do not respond) and the same
  connections as a table behind **Show the connections as a table**. On a phone each column is a
  list. Whether a backend answers is the result of a one-second connection to its loopback port,
  cached for ten seconds; a draft is drawn from the unsaved text but its backends' state comes from
  the saved file.
- **Try a URL**, above the diagram, takes a full address or a path
  (`https://shop.example.com/api/`) and asks the backend which server and location answer it
  (`POST /api/sites/{domain}/route`); nothing is requested, the web server's rules are replayed over
  the file (or the unsaved draft). Only that path is lit and animated, a sentence says which
  location of which server answers the URL (or that the server answers it itself because no
  location matches, or that no server takes it), and a numbered explanation gives each step of the
  web server's choice: which server by port and name, which location by exact match, longest
  prefix or regular expression, and what that location does. It is the way to see why
  `/api/v1/auth/login` reaches the location it reaches and not the one you expected.

A server whose Noust is older than 3.2 (a 3.1 node reached through a 3.2 central) has no such
views: the page says "This server runs an older Noust" and **Edit as text** goes back to the
editor, which works as before.

### Backups

Five tabs: **Backups** (storage used per application, every backup: verify, restore, delete;
"New backup" with the same options as `noust backup create`), **Schedules**, **Volumes**,
**Noust** and **Destinations** (remote places, their test and their key). **Volumes** backs up an
application's data on its own: its persistent folders and the Docker volumes of its stack, picked
from what it declares (or everything it declares), on a schedule with its own retention, sent to
the same destinations, taken live, with the application paused or with it stopped, run by hand as
a job, and restored over what is there after you type the application's name (a safety copy of
what is there is taken first). **Noust** backs up Noust's own state (the store, the
configuration, the secrets and keys, the certificates and the unit files it generated) into one
file sealed with a passphrase: by hand (the passphrase is typed twice and kept nowhere) or on a
schedule (the passphrase is then kept in the secret store for the timer, and you keep a copy
outside the server), checked, and sent to a destination. A restore replaces the store the console
runs on, so it is never done from the browser: **How to restore** shows `noust self-backup
restore` and `noust restore-server --from`, and what they ask. **Move to another server** (on the same
tab) copies the applications, their data, the standalone databases and Noust's own state to a destination
the new server reaches: it shows what would be copied, what stops it and the engines the new server needs,
asks for the passphrase the state is sealed under (empty uses the stored one), queues the copy as a job and,
when it ends, shows each piece (a failed one in the manager's own words) and the commands to run on the new
server; nothing on this server is stopped or changed. A backup's history drawer lists the **restore tests**:
what was unpacked and loaded into a temporary database, when, and what it did not try. See
[disaster-recovery.md](disaster-recovery.md). The backup an update of a Docker
Compose stack takes first holds a dump of each database the stack runs, unless the application
turned that off under Settings > Automation; putting the databases back is `noust backup restore
BACKUP_ID --databases-only` from a terminal. Restoring only the files past a deployment that
changed the database schema asks first, as going back does (see
[above](#one-application)).

### Activity

Every job and every audited action on this server in one timeline, newest first, filterable
by kind, result and actor. A job opens its log. What builds and what waits leads the page as the
[deploy queue](#the-deploy-queue-and-cancelling).

### Logs

One search over the logs, from one toolbar: a query box (`/` focuses it), **Where from** (the
applications, with the journal's lines and the containers' output, or the **System**: the journal of
any service of the machine), **How far back** (**Live** first; **This boot**, **Previous boot** and
**Since the last deploy** are ranges too), the syntax help and **Copy link to this search**. Credentials
are masked, and everything is in the address so a search can be sent to somebody, with how many lines
matched and how many were examined. An application's Logs tab has the same toolbar. On a central,
**Every server** searches all of them, each line naming its server. `/server/logs` opens the System
source. See [logs.md](logs.md).

### Server

Six tabs, each with its URL (the system's journal is in [Logs](#logs)):

| Tab | What it has |
|---|---|
| Overview | What needs attention, then the machine at a glance; fits one screen |
| Updates | Pending updates, security first; apply all or only security; refresh; "a reboot is due" and why; services on replaced libraries; automatic updates; the history of runs |
| Security | Hardening checks with their fix or exact steps, accepted risks; SSH (effective settings, keys, safe fixes that revert unless confirmed); the firewall against the ports that really answer; fail2ban |
| Storage | Real filesystems, what takes their space, clean-ups, swap; **Automatic cleanup**, the policy this server runs by itself (schedule, how full a disk must be, which actions) with a Run now, and the history of every cleanup |
| Services | Every systemd unit Noust manages ("Show all units" lists the rest, read-only); a service's page has its live journal, controls and a unit editor checked with `systemd-analyze verify`. A unit carries tags like an application (**Edit tags...** in its menu, a Tag filter, chips under its name) |
| System | Time and synchronisation, host name, the operating system and its end of life, processes, network, reboot and shut down (now or scheduled), and the resource monitor |

`/services` redirects to Server > Services.

**Searching the logs.** The search box of Logs takes a query, judged by the server against what
the journal returns (`GET /api/server/logs/search`, `noust server logs -Q`): nothing typed is part of a
command line, and no regular expression is run.

| Write | It finds |
|---|---|
| `error timeout` | entries whose message has both words, ignoring case |
| `"connection refused"` | that exact phrase |
| `@unit:nginx` | entries of that service (`.service` optional; `*` is a wildcard, as in `@unit:noust-*`) |
| `@app:shop.example.com` | entries of the services that application runs as |
| `@level:err` | that level or worse (`err`, `warning`, `info`, or 0 to 7) |
| `@pid:4821` | entries of that process |
| `-debug`, `NOT debug` | leave out what says that |
| `a OR b`, `(a OR b) c` | either; grouping (`AND`, `OR` and `NOT` in capitals) |

On the System source, the Service and Level pickers are shortcuts for `@unit:` and `@level:`, and the page says the query as the
server understood it ("Understood as `@unit:nginx AND @level:err AND NOT timeout`") with how many entries
it looked through. A search examines the newest 5,000 entries of the range (20,000 on request) and says
when older ones were not examined, so a narrow range or `@unit:` finds what a wide one cannot. A query
the server cannot read is answered in its own words, with where. The range can also be a stretch carried
from elsewhere: **What happened here** opens the timeline of the stretch being searched, and the
timeline's **Search these logs** brings its stretch (and its application, as `@app:`) back to Logs. **Click a line, or select it with the arrow keys and press Enter, to see it in context**: the entries
the journal logged just before and after it, from every service or only its own, with the one chosen marked
(`noust server logs --around CURSOR`, with the cursors `--json` prints).

**Time zone.** On the System tab, **Change time zone** opens a dialog with a searchable list of the
zones the managed server itself knows (`GET /api/server/clock/timezones`; on a central, the
selected server computes them, not your browser). Type a name, a city, an abbreviation or an
offset (`madrid`, `cest`, `+2`, `utc+1`) and the list narrows; zones are grouped by region, each
option shows the name (`Europe/Madrid`), the city and its offset and abbreviation now
(`UTC+02:00 · CEST`), and the zone in use is marked **In use**. Under the list, the time the
server's clock will read with the zone you chose. Cron jobs and scheduled backups run at local
times, so their timers move with the zone, and the toast says how many moved. A server whose Noust
is older than 3.2 does not list its zones: the dialog says so and the name is typed, as before.

**Processes.** Click a column header (Process, PID, CPU, Memory) to sort; the order is applied by
the server, each column in its natural direction (CPU and Memory busiest first, PID and Process in order),
and the CPU and Memory headers sort the view grouped by service. The button under the table says how many there are ("Show the first 50 of 213") and keeps
the rows already listed while the longer list loads. The console never signals a process.

**Automatic cleanup.** The Storage tab's **Automatic cleanup** card sets what this server cleans by
itself: a schedule (a systemd timer, `noust-storage-clean.timer`), a threshold (it runs only when the
fullest writable disk is at least that full, 80% by default; 0 runs it every time) and the actions, from
the same closed list as a manual cleanup: the journal, the package cache, the old deploys kept for
rollback and, only if the policy says it accepts that once, the Docker build cache older than a week and the
images with no name and no container. Removing one named Docker image and Docker volumes (which hold
databases) are never scheduled. **Run now** runs the policy at once, as a job, whatever the disks hold.
Every cleanup, by hand or by the timer, leaves one row in the history beside it: when, what started it, the
fullest disk before and after, what each action freed or removed, and the tool's own words for one that
failed, so a cleanup that took something you missed can be reconstructed. From a terminal:
`noust server cleanup-schedule show|set|run|history` (`set --enable --schedule daily --threshold 80
--action journal --action pkg-cache`).

### Settings

A side navigation with a page per section, in six groups that say whose they are. **This server**,
**Monitoring** and **Developers** are the selected server's; **People and access**, **Governance**
and **Fleet** are the instance's and stay the same whichever server you choose. From twelve sections
the list has a filter that also finds a section by the name it had before, and on a phone the list is
a page of its own. Your own password, authenticator app, passkeys and sessions are not here: they are
**Account** and **Account > Sign-in security**, in the avatar menu.

| Section | Group | What it has |
|---|---|---|
| General | This server | Applications directory, web server, certificate email, backups, the server's name, and the console address `--open` links use; and **Deploy freeze**, which holds every deploy on the server for a stretch ([delivery.md](delivery.md#freezing-deploys)) |
| Channels | Monitoring | Whether notifications are on, at the top (amber when they are on but no channel is set up; see [Features that are on or off](#features-that-are-on-or-off)); channels (webhook, Slack, Discord, Telegram, email, Pushover, ntfy, Gotify, Microsoft Teams, Mattermost, Matrix, Lark, PagerDuty) in drawers, with a test each; email by SMTP or through Resend, Postmark, Brevo or Amazon SES; per channel its own events, its route, quiet hours, a **cap on messages** and a **daily summary**; the record of deliveries with why one is held; which events notify, among them **Deployed with warnings** (`deploy_hook_failed`), **Application not answering** (`app_unreachable`) and **Application answering again** (`app_recovered`), all on by default; private destinations allowed |
| Integrations | This server | The GitHub App, the address for webhooks, the address and an entry to paste for a metrics scraper (Prometheus, Grafana Alloy, an OpenTelemetry Collector), and the server's **deploy keys** (each with the repositories it reaches and the applications built from them) and the **host keys it pinned**, which can be forgotten after comparing the fingerprint |
| Alert rules | Monitoring | Rules that fire when a measure stays past a limit for a while, the alerts firing now, and the built-in checks (disk, certificates, backups, patches, tokens) as boxes whose number shows only while they are on. A firing alert is **taken** by whoever is on call, with who and when, and **reopened**; the same buttons are in the inbox. See [notifications.md](notifications.md#alert-rules) |
| Logs and metrics export | Monitoring | Where the logs of applications are sent besides the journal: Grafana Loki, Axiom, New Relic, an HTTP endpoint, OpenTelemetry or a remote syslog; how each destination is doing and what it answered when it failed, verbatim; add, edit, turn off, test with one line, remove. See [runtime.md](runtime.md#log-shipping). Below it, **Keeping the logs**: how many days and how much disk the journal may take, whether it survives a reboot, and a way back to journald's defaults ([logs.md](logs.md)) |
| Probes and status page | Monitoring | Addresses the monitor asks from outside the application: where each stands, what it last answered, its uptime over a day, a week, a month and a quarter, its incidents, and a button to ask it now; and the **public status page** made of the ones marked public, kept up to date, titled, and put on a name of its own through a job. See [probes.md](probes.md) |
| Variables | This server | The server's shared variables, which every application can refer to as `{{server.NAME}}`; the team's, the project's and the environment's are on their own pages (below) |
| Secret managers | This server | Vault, Doppler and Infisical, which a variable refers to with `{{secret:NAME/path}}`: the address, the settings of the kind, the team that may name it and a token that is written and never shown again; **Test connection** proves it answers. A secret's value is fetched at every deploy and never kept. See [variables.md](variables.md) |
| About Noust | This server | Version and updates; **Automatic updates** (whether this server installs Noust's own new releases by itself, the releases it takes, the hours and days, the next window and what the last run did; saving writes or removes a timer, in sudo mode; [release-policy.md](release-policy.md#updating-by-itself)); installation, CLI equivalents, links |
| Servers | Fleet | Every server, its reachability, version, ceiling and labels, and, under its state, how long it has been silent or why its monitoring is paused; **Add a server** takes an address and a credential used once, confirms the host key, installs Noust if it is missing and joins the server (below), or, a link away, walks through `noust fleet authorize` and the join code, or, for a server behind NAT, makes it dial in; **Create at a provider** opens the wizard of Settings > Cloud providers; test, pause monitoring and remove |
| Cloud providers | Fleet | The provider accounts (Hetzner, DigitalOcean, Vultr) with their tokens as secrets, the servers this central created and why one did not join, and the cloud-init library new servers boot with (below) |
| Console address | This server | Publishes the console on a name of its own over TLS, or takes it down (below) |
| MCP | This server | Turns the endpoint for MCP clients on or off, switches its groups of tools, lists the tools with the permission each needs ([mcp.md](mcp.md)) |
| Manifest | This server | Previews what applying a manifest of applications would change here, then applies exactly that as you ([manifest.md](manifest.md)) |
| API reference | This server | Every route the server answers, with the permission it needs, what it takes and answers, a curl to copy, and a way to try the call with your own session (a change asks first; the status, the headers and the server's words are shown) |
| Accounts | People and access | Accounts, roles, invitations, separation-of-duties exceptions (with `accounts.read`) |
| Roles | People and access | Who holds which role and where (an environment, a project, a team, the server), granting and revoking, and the roles of your own (with `accounts.read`; [teams.md](teams.md#roles-where-they-are-needed)) |
| Single sign-on | People and access | The identity providers (OpenID Connect, SAML), directories and SCIM provisioning sources (added, tested, removed), the people linked to them, and the switch that turns passwords and passkeys off, naming the accounts that keep theirs ([sso.md](sso.md)) |
| Controls | Governance | The security profile in force and the sign-in policy; the compliance frameworks (ENS basic, medium and high, ISO/IEC 27001, NIS2, CIS, SOC 2, GDPR) and every control with its value and who forces it ([COMPLIANCE.md](COMPLIANCE.md)) |
| API tokens | Developers | Your tokens: issue (shown once), narrow, bind to a team, a project or an environment, list (with where each reaches and when it expires) and revoke |
| Approvals | People and access | Four-eyes requests: yours, and those waiting for you to decide |
| Audit log | Governance | The audit trail, its verification, destinations and reviews (with `audit.read`) |
| ENS report | Governance | `noust ens check` and the evidence report |
| Central | Fleet | The role, the certificate and the seal |

**Add a server** opens on the guided way: a name, the address and port, the account (root, or one
with passwordless sudo), what the central may do there (read, deploy or admin, and whether it may
also manage the server itself), and how to get in, a password or a private key. The credential is
used once: it reaches ssh through its environment or a private memory-backed file, is never
stored, logged, kept in the job's record or put in the audit trail, and the page forgets it the
moment the request leaves. Before anything else is sent, Noust asks the address for its ed25519
host key and shows the fingerprint, to compare with `ssh-keygen -lf
/etc/ssh/ssh_host_ed25519_key.pub` on the server; the join must carry that same key. Then it is a
job whose output (the installer's included) streams in the dialog, ends on the server joined, and,
if it fails, shows ssh's own words and goes back to the details with the credential empty.
**Authorize it with a command on the server instead** is the way that never gives the central a
credential at all.

**Create a server** (Settings > Cloud providers, or Fleet > Servers > Create at a provider) is a wizard in
five steps: the name and the provider account; the location, size (with its processor and monthly price)
and image, read from the provider now; the options its create call has (public addresses, backups,
firewalls, networks, SSH keys of your own, boot scripts from the library, delete the machine if it does not
join); a review that says what it costs from the moment it is created and shows the cloud-init document it
will boot with; and the job, whose log streams until the server has joined. Adding an account asks for the
token once, checks it with the provider and never shows it again, only its last four characters; the servers
created here stay on the page with the reason when one did not join, and can be powered on or deleted at the
provider (the name typed to confirm).

**A server behind NAT** (Add a server, then "It cannot be reached from outside") is added in three steps:
the name and the address it will dial, which gives it a port of this central and the command to run on it
(with this central's SSH host key, to compare by eye); the link code it printed; and the result, once it has
connected and answered. If this central has not been prepared for it, the dialog says what is missing and the
command that fixes it (`noust node link setup`). A server that dials in shows "Dials in: port N of this
central" where another shows its SSH address.

**Console address** publishes the console at a name that points at this server: an nginx site, a
Let's Encrypt certificate, the console told to trust nginx, and an allow-list if you give one;
there is no plain-HTTP form. The page lists what is checked first (a second factor on every
account that can sign in, with the accounts that lack one named; the ENS profile, which keeps the
console on loopback unless you force it with an allow-list; a name that is not an application's
or a site Noust did not write), the dialog previews what will be done, and publishing and
unpublishing are jobs followed in the page (sudo mode). Unpublishing removes exactly what
publishing made and leaves what was already there.

On a server without a fleet, every section is the server's own. What a server's own accounts,
tokens and second factor are cannot be changed from a central; the page says how to manage them
on the server.

![Fleet > Servers](assets/console/fleet-servers.png)

Writing any setting needs sudo mode.

## Features that are on or off

A page that configures a feature says its state beside the setting, as a pill: a colour, a glyph and a
word at once, so it reads without reading the sentence under it. The setting is a row, with the
switch at its end and, when turning it on or off has a risk, the risk in one line and the confirmation
that goes with it.

| State | Shown as |
|---|---|
| On | The running green, a glyph and "Enabled" |
| Off | Grey, a glyph and "Disabled", with what being off means for you |
| On with a problem | Amber and a warning triangle, never green: it is on but cannot do what it says ("Enabled, but no channel"), with the action that fixes it |

The state is a row on Settings > Channels, Console address, MCP, Approvals and Automatic updates (in
About Noust), the second factor in Account > Sign-in security, scheduled backups on the Backups page,
automatic updates on Server > Updates, and in an application's settings for instant rollback,
zero-downtime deploys, builds in the sandbox and pull request previews. See [DESIGN.md](DESIGN.md).

## Keyboard

| Keys | Does |
|---|---|
| `Ctrl K` / `Cmd K` | Open the command palette, from anywhere |
| `g` `o` | Go to Overview |
| `g` `p` | Go to Projects |
| `g` `a` | Go to Applications |
| `g` `b` | Go to Databases |
| `g` `n` | Go to Domains |
| `g` `k` | Go to Backups |
| `g` `c` | Go to Cron |
| `g` `l` | Go to Logs |
| `g` `y` | Go to Activity |
| `g` `v` | Go to the server (on **All servers**, to the fleet's Servers) |
| `g` `f` | Go to **All servers** (a central, from a server) |
| `g` `u` | Go to the fleet's Updates (on **All servers**) |
| `g` `s` | Go to Settings |
| `g` `d` | Go to the application's Deployments tab; outside an application, to Activity |
| `/` | Focus this page's search, or open the palette when it has none |
| `?` | Show the keyboard shortcuts |

Two-key sequences are typed one after the other within about a second. They do nothing while
you type in a field or while a dialog is open. Inside a log viewer, `Ctrl F` / `Cmd F`
focuses its search; `Enter` and `Shift Enter` step through matches.

The **command palette** searches anything the console lists, by any name it answers to, and says
where each page lives: pages and each Settings section (an old name still finds it); applications (domain, name and type, with their state); databases;
sites (by every name they answer on); certificates (by every name they cover, with the time
left); services; the other servers; the newest deployments (by application, commit or
message); and actions, the ones that create something first: new application, new application
from a recipe, import an application, adopt a stack, new database, add a server, then change
theme, keyboard shortcuts and sign out. `>` at the start lists only actions, and `@` changes the
scope (a server, **All servers** or **This central**). The last three places it opened come first. It
reads its lists the first time it is opened, only
the ones your account may read, and reads them again after a job ends. Arrow keys or
`Ctrl N` / `Ctrl P` move, `Enter` runs, `Esc` closes.

The palette also **does** things to an application, not only opens it: type the verb
(`restart shop`, `stop shop`, `start`, `roll back shop`, `update shop`) and the result is
"Restart shop.example.com" under Operations. A verb is the application's own button, run by the
same code: stopping asks the same one question, going back opens the same choice of version, an
update with nothing new asks the same "rebuild anyway", and "Confirm it's you" appears when the
server asks for it, exactly as for the button. A verb the account may not do in the
application's team is not offered, a stack running outside its unit has no Start or Stop (the
banner's "Hand it back to Noust" is the way), and typing only an application's name finds the
application, never every verb. On an application's own page its verbs are listed before anything
is typed. Deleting is not in the palette: it needs the typed name, on the application's page.

Every list of more than 20 rows is paged: 20, 50, 100 or all rows to a page, chosen under the
list, remembered for that list in the browser, with "Showing 21–40 of 57". An application's
page keeps its daily verbs in its header: Restart, Stop or Start (by its state) and Roll back
beside Update, and on a phone as icons that keep their names; Diagnose and Delete are in
More actions.

## Sudo mode

Destructive and credential-changing actions (deleting anything, restoring a backup, revealing
or editing an `.env`, moving to instant rollback, changing limits, editing a unit or a site,
writing an application's deploy hooks, adopting a Compose stack, SQL in write mode, server
changes, managing accounts, changing settings, issuing a token) ask
you to confirm it is you: a dialog titled "Confirm it's you" asks for your passkey, or a code from
your authenticator app or one of your backup codes (for the access token, its code, or the token
itself when it has none). You already gave your password and a code when you signed in, so one
factor is enough, unless the server asks for the password again (`auth.sudo.require_password`,
always under the ENS profile), in which case the dialog also shows the password field. The
confirmation stays open while you keep working: every destructive action extends it, for up to 2
hours from when you confirmed (15 minutes without one, 2 hours at most, under `auth.sudo.*`; 10 and
30 under the ENS profile), and the dialog says so with the server's numbers. The action you were
taking is retried once confirmed. Flows that already know they need it, such as deleting an
application, ask before their own confirmation dialog, so only one is ever on screen.

When approvals are on, an action that needs a second person becomes a request instead of
running: the console says so, it appears under Settings > Approvals for whoever may decide it,
and once approved you run it again from the same place, once.

## Live updates

The console holds one Server-Sent Events stream for the machine strip, charts, application
states, job progress and notifications, and opens a WebSocket for a log or a running job
while you look at it. The machine strip says **Live** while that stream keeps the readings current and **Reconnecting** when it
does not. If the connection drops it reconnects with backoff and refreshes
everything it shows. Log viewers render ANSI colours, search, follow the newest line (pausing
when you scroll up, with a "Jump to latest" button), wrap, copy and download. When the output has
levels, a chip for each (error, warning, info, debug, with its count) keeps only the levels you
choose, the line numbers stay those of the whole output, and copy and download take what is shown;
the time column can be hidden, and the viewer can take the whole screen.

A job runs on a thread of the console, so a console that restarts ends the jobs it was running:
they are marked "Interrupted by a panel restart". The exception is work that runs in a transient
systemd unit of its own (applying operating system updates, and Noust updating itself), which
keeps going when the console restarts, and a `noust` package among the updates is precisely what
restarts it. The job records its unit, and the console that starts again follows it
instead of failing it: when the unit ended well the job completes with the unit's output appended
to its log, when it failed the job fails with systemd's result and the unit's own words, and when
it is still running the job stays running, its log grows as the unit writes, and it ends with the
unit, or gives up after a deadline and says how to follow it. A job with no unit recorded is still
marked interrupted.

## Themes

Light, dark, or the system's (the default), from the session menu or the command palette.
The choice is kept in the browser's local storage (`noust.theme`) and applies to every open tab.

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
- A site's diagram has a written summary under it and the same connections as a table; every
  element can be focused from the keyboard and has an accessible name that includes its state.
- With `prefers-reduced-motion`, transitions are instant and the moving dashes of the diagram
  stand still.

If something in the console is not usable with your assistive technology, please open an
issue on GitHub.
