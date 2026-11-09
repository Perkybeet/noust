# Noust 4.2 changelog

Changes since Noust 4.1.0. What to do before updating is in [UPGRADING-4.2.md](UPGRADING-4.2.md);
what a client of the HTTP API can notice is in [API-CHANGELOG.md](API-CHANGELOG.md).

4.2 puts the console in order. It adds no area of the product: it takes what 4.1 had and puts each thing
where a person looks for it, with one name each, and draws it before it explains it. One frame on every
screen: the main bar with titled groups and a column on every area, Network and Security as areas of
their own, and two contexts, a server or the whole fleet. Home becomes panels of widgets each person
arranges and keeps. What runs in the background stays in sight from any page, on this server or on
another. The server's software is one page, with installing and removing that refuse what something
still uses. Pages are composed of pieces that say what each thing is, and a hint to the terminal appears
only where the console cannot do it. Signing in is redrawn, and a fault is reported from the console in
three steps. It is a minor version: the HTTP API only grows, the command line keeps every command, and
the store moves to schema 17.

## Before you upgrade

[UPGRADING-4.2.md](UPGRADING-4.2.md) has the order of an update and the way back; this is what to know
first.

- **The store moves to schema 17, and 4.1 cannot open it.** The upgrade keeps a copy of the store beside
  it, as before. Going back to 4.1 means putting that copy back (`noust store compat --to 4.1.0`;
  [downgrading.md](downgrading.md)).
- **A token acts in sudo mode only if it was made to.** A token made for an account used to pass where the
  console asks "Confirm it's you" whenever the account could; now only a token created with `--elevated`
  (`allow_elevated` in `POST /api/auth/tokens`) by someone who governs accounts does, and every use of it
  there is in the audit trail. A pipeline that deletes, restores or changes access through a token needs
  a token made that way.
- **Uninstalling a database engine is refused while it holds databases.** `noust db engine uninstall` and
  the console name each database, from the store and from the engine itself; drop or move them first.
- **The console's pages moved, and the old addresses lead to where they went.** An application's eighteen
  settings subsections are four pages of its column, Published ports, Tunnels and the firewall are in Network,
  Server > Security is Security, Server > System is Server > Updates, Databases > Servers is Databases > Engines,
  Settings > Controls is Settings > Compliance and Settings > Connections is Settings > Integrations. A bookmark
  keeps working and the palette finds a page by its old name; the whole table is in
  [UPGRADING-4.2.md](UPGRADING-4.2.md#what-you-will-notice).
- **A fleet in the middle of an update works.** A 4.2 central drives servers still on 4.1: the fleet's
  task list, its software and its updates list mark a 4.1 server "needs 4.2" and go on with the rest.

## The new console

- **One frame on every screen** (items 108, 109, 112). The main bar has titled groups (Applications and data,
  Network, Observe, Protect, Infrastructure) and folds to its icons; every area has a column beside it with its
  sections, and a resource opens inside it. Network and Security are areas of their own. A central has two
  contexts, one server or the whole fleet, and the frame says which one is on screen. On a phone the column is a
  selector at the top of the page and the bar sits at the foot.
- **Home is panels of widgets** (item 113): each person arranges, resizes and keeps their own, for one server or for
  the fleet, and starts from a panel that is useful on the first visit. The map leaves Home.
- **Background work stays in sight** (items 111, 114). A task centre in the top bar follows every job and long
  operation of every server from any page: what runs, how far it has got, how it ended. A task can be pinned to
  the foot of every page, has a page of its own, and Activity > Tasks keeps the history. The browser says once when
  something ends while the tab is away, if the person allows it.
- **Signing in is redrawn** (items 108, 129): one slab with a drawing from the side, the other ways in as equal
  doors under the password form, and the same frame on every screen around it. The drawing's space can show the
  operator's own logo or picture (Settings > Sign-in image, or `noust console sign-in-image set`): PNG, JPEG or WebP
  up to 1 MB, judged by its content, its metadata stripped, filling the space or shown whole, with its own
  alternative text.
- **Server > Software** (item 118) lists what the server is made of, installs as a task and removes only what
  nothing uses, saying what does. One "X is missing" notice, with the way to install it, replaces the ports', the
  tunnels' and the firewall's own.
- **Pages say what each thing is before they explain it** (items 121, 122, 123): sections with a title and one line,
  a first-use view that leads to the action, cards for resources, a "?" beside a rule that is long, and a hint to
  the terminal only where the console cannot do it, with the reason. What a person cannot do is said on the page,
  not discovered on a 403. Cloud, Image registries, Deploy keys and Integrations are redrawn with them.
- **Applications** (items 99, 116, 120, 121, 122). An application's column lists its sections once, in five groups (Use,
  Configure, Fix, Move elsewhere, Danger), and its eighteen settings subsections become four pages: Code and build, Deploys
  and triggers, Resources and running, Access and protection, each with one save bar and its rarer parts folded a press
  away (an address with `#part` opens its fold, and every old address leads there). The list is one table with the tags on
  the name's line and views by project and by state. A new application starts from five cards (a repository, the catalog, a
  container image, files, something that already runs here) and lands on its own page while it deploys for the first
  time; one that fails says why. Delete names each thing it would take (volumes, databases, an adopted directory), with nothing ticked, and an
  adopted stack can be let go instead. Export is a section of its own.
- **Databases** (items 79, 112). A database's whole path lives in its column, with the sections its engine has
  (a query console only for an engine that speaks SQL). One dialog makes a database, empty or from a dump; a stopped
  engine is a state of its own with "Start the engine first"; and upgrading an engine says before it starts that it
  makes a new instance and moves the databases into it.
- **Domains and Network** (items 112, 119). Domains says where each name leads, whether it has HTTPS and where its DNS
  points, and one dialog adds a name to an application, as a redirect or as a site of its own. One dialog opens a port,
  from Published ports, from a row of who listens, from the firewall, from Home and from the palette, and says what
  listens on the port as it is typed and what it will change. Listening ports is the one table of who listens, a port
  only the kernel reports included; the firewall says who made each rule and names the provider's firewall and Docker's
  beside it. Tunnels and Interfaces are redrawn.
- **Server and Security** (items 112, 125). Status answers "why is it slow": four readings, what uses the most now and
  whose it is, what is not well with the action that settles each, and how many ports are open to the outside, a press
  from Network. Machine, Processes and the resource monitor are sections of their own, each titled by itself with its
  actions beside the title. One page updates Noust and the system, with automatic updates switched beside its title.
  Security has five sections: checks that lead to where each is fixed, SSH, Blocks (the list first, searched by
  address, unblocked by row), and two new pages, Web firewall (WAF) and Access and sessions. On a central, every server
  of the list is updated from its row to the version it offers, or all at once.
- **Settings** (items 109, 110, 121). The column groups the sections by what the person is doing and says "All the fleet"
  once for a group that is all of it. Compliance shows the frameworks the server offers with nothing in red until one is
  on, turns one on through a dialog that says what changes, and keeps a report per active framework (Settings > Controls
  is Settings > Compliance now). Access security is a page of its own, and single sign-on starts from "With the company
  account". Turning notifications off asks until when.
- **Report a problem** (item 127), from the "?" in the top bar, from More on a phone and from the screen of a page
  that broke: three steps, one zip written in the browser (the server's support report, the tab's last errors, images
  and what the person wrote, everything shaped like a secret masked with the server's own shapes), and GitHub's form
  opened filled in. Nothing is sent by itself.

## Home, server side

- **Each account keeps its panels.** `GET/PUT/DELETE /api/auth/dashboards` and their order hold the name of
  each panel, whether it follows the server or the fleet, and its widgets on a grid of 12 columns (version 1
  of a closed layout: 40 widgets a panel, 20 panels an account, 32 KB). They live under `/api/auth`, so a
  central never forwards them: the person's panels are the person's on the server they sign in to. The
  master token is no account and keeps its panels in the browser (item 113).
- **The map leaves the console, not the API.** `GET /api/map` and `noust map` answer as before.

## Background work

- **Every job says what it is**, as a sentence of the server's catalog (`title` with a code and its values)
  that the console says in its language, for each of the job types (item 114).
- **Long operations that are not jobs are announced**: starting, stopping and restarting an application, a
  service or an engine, running a scheduled task, verifying a backup, rebooting, applying the fleet's
  settings and the rest of the list in [API-CHANGELOG.md](API-CHANGELOG.md). The `operation` event and
  `GET /api/operations/active` say what runs, who started it and how it ended; none of those routes changes
  its answer.
- **A central sees what every server runs now** (`GET /api/fleet/jobs/active`), one row per server, and a
  server that does not answer or runs 4.1 is one row that says so, never a failed page.

## The server's software

- **One list of what the server is made of** (item 118): 25 components Noust uses (nginx and Apache, certbot,
  Node and PHP, Docker, the database engines, cloudflared, the WAF, Knot, fail2ban, rclone and the rest), whether
  each is installed, its version, whether Noust put it there, and what on the server depends on it.
  `noust server software list|install|remove`, `GET /api/server/software` and, on a central,
  `GET /api/fleet/software`.
- **Noust keeps a ledger of what it installed**, with the repository and the key it added for each (schema
  17), so it can tell what it put there from what was there before.
- **Removing asks what it would take with it.** `remove --plan` (`.../impact`) lists what depends on the
  component and what the package manager would take (apt, dnf and zypper are simulated first); the removal
  is refused, with the reason, while an application, a site, a database or another component uses it. It
  never purges what a package left in `/etc`, never removes Noust itself, systemd, sshd, the firewall, git,
  curl or gnupg, and something Noust did not install needs `--confirm-unknown-origin`. Sudo mode and
  `server.host_access` are required.

## Compliance

- **A server that never asked for the ENS is not judged against it** (item 110). With no ENS framework on,
  `noust ens check` exits 0 and says so and `GET /api/ens/check` answers `enabled: false`, so a fresh install
  no longer opens on a report in red. `noust compliance check --framework ens-media` still says how far a
  server is before turning it on, and the evidence report judges in any case.
- **A report per active framework**, with its date, host and version, downloadable as Markdown with its
  SHA-256 (`GET /api/compliance/check?format=markdown`).
- **A `security.profile` that is not a profile says so.** A value Noust does not know (`off`, `none`, a typo)
  is still read as `ens-medium` on purpose, so a typo cannot turn a security profile off; now `noust
  compliance status` and `GET /api/compliance` say what was read, that it is not a profile, and the two
  commands that settle it, instead of showing ENS MEDIA on "by profile" with no reason.

## What the server says, in the operator's language

More of what the server tells the console carries a code beside its English text, so the console says it
in the person's language: published ports and why they cannot be published, every check of the diagnosis
and its probable cause, a Compose stack's networks, the cleanup plan, swap and the storage history, what a
site action answered, the new-application check, and the steps and log lines of the standard jobs and of
an application's own pages. The English text is unchanged, and what a system printed (nginx, systemd, a
package manager) is still shown as it printed it.

## Fixes

- `PUT /api/config/smtp` no longer empties the recipients when the body does not name them, and the same
  SMTP password sent again with a new server or transport is accepted.
- A change made to `config.yaml` at the terminal reaches the running console without a restart, so the
  console sees a compliance framework turned on or off from the command line.
- `noust token revoke` takes `--yes`, like every command that asks.
- The list of running operations asks for the session by name, as every route of the jobs does.
- An engine uninstall also removes the repository and keyring Noust added for it on apt.
- A service created from a unit written by hand (`POST /api/services` with `raw_content`) is checked with
  `systemd-analyze verify` before anything is written, as the unit file page already did, and systemd's own words come
  back when it refuses. The unit the console's form writes carries Noust's marker, without which every unit created from
  the form was refused with a 409.
- Notifications can be paused until a moment (`notifications.muted_until`, a time with its zone): after it everything is
  sent again with nothing to remember. The console's switch and Alerts ask until when.
- On a central, the Result filter of every server's activity filters, every server's alerts put the ones firing first,
  and Open on a server finds the job.

- Deleting an application offers what the API always took and the console never sent: its volumes, its
  databases and, for an adopted stack, its directory, each unticked and named, read from what deleting would
  touch. An adopted stack can be let go instead.
- A database whose engine is stopped says so with "Start the engine first", instead of loading shapes that never
  ended; upgrading an engine says before it starts that it makes a new instance and moves the databases.
- A site's history restores a version through the restore route, and a new site's warning is chosen by the
  server's code, not by looking for its English.
- A service that is not Noust's shows no Logs section the server refuses, and no bare 403 in its configuration to
  an operator who may not read secrets; Logs > System says what happened on a 400 or a 403.
- A new application lands on its own page already drawn, with its first deploy, and the page of a task, a
  service's unit and Noust's own backups no longer move as they load.
- What a system printed in a log viewer is never translated, by the console or by the browser, and what the
  process monitor notes about a process is said in the person's language.
- Masking secrets in text leaves an Authorization header that was already masked as it was, instead of hiding
  its scheme word on a second pass.
- About reports a problem through the console's own three steps, and the session menu says Documentation and
  Shortcuts once.

## 4.2.1

- **The console no longer runs out of open files.** Each of the console's worker threads kept its own
  connection to the store and to the metrics, and a thread that ended left its connections open until a
  full garbage collection, which a busy process seldom runs. A central reached its limit of 1024 open
  files in under three days and then answered every request with "Too many open files": the fleet said
  "Internal Server Error" and a page "Failed to fetch dynamically imported module". A thread's
  connections are now closed once it has ended, and the process observations a request reads close
  theirs when it is answered. On 4.2.0, restarting `noust-web` is the way out until the update.
