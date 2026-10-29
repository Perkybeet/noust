# Operate the fleet

Part of [The Noust central](CENTRAL.md).

## Seeing the whole fleet

The fleet pages and `noust fleet` ask every server at once, each with its own deadline, and show
what came back: a server that is down, too old or refuses the operator is one row with its
state and its own words (ssh's, or the server's API error), never an error for the whole page.
When a server cannot be reached, its last good answer is shown with its age.

```bash
noust fleet status                       # every server: reachability, version, apps, units, certificates
noust fleet apps --status failed         # every application of every server
noust fleet certs --expiring 21          # certificates, the soonest to expire first
noust fleet backups                      # each application's newest backup, the gaps first
noust fleet updates                      # Noust and system updates pending on each server
noust fleet activity --node vps1         # what happened lately
noust fleet logs error --since 1h         # search the logs of every node at once (docs/logs.md)
noust fleet metrics --metric memory --window 7d   # one metric of every node, side by side
```

In the console, **All servers** in the scope selector has the same views as the sidebar (Overview,
Servers, Applications, Certificates, Backups, Updates, Activity), and each row links to the page on its
server. On a hub, the fleet's Overview is the home page. The selector switches every other
page to one server, and each page shows that server's name above its title.

Over the API, the same resources (applications, databases, sites, certificates, backups, scheduled
jobs, logs, alert rules and activity) are one route each under `/api/views`, for the central and
every server at once, with the server as a column and a filter (`node`, and `@central` for the
central itself) and a search that finds a thing on whichever server it is. A Noust with no servers
to manage answers its own rows and says it has no fleet. Servers that refuse the same permission
come once, with how many they are, which, what is missing and how to give it, instead of one
paragraph each. See [api.md](api.md#a-resource-across-the-servers).

## Acting on several servers

```bash
noust node label vps1 env=prod region=eu     # labels live on the central; the server never sees them
noust node label vps1 region-                # remove one
noust fleet run certs_renew --label env=prod --plan
noust fleet run apps_update --label env=prod --serial 25% --max-failures 1 --canary vps1
noust fleet run os_updates --nodes vps1,vps2 --option scope=security
noust fleet run noust_update --label env=prod --option target_version=4.0.0 --plan
noust fleet jobs                             # the fleet jobs, and each server's outcome
noust fleet retry JOB_ID                     # again, on the servers that failed or were not reached
```

Actions: `certs_renew`, `backups_run`, `backups_verify`, `apps_update`, `apps_restart`,
`noust_update`, `index_refresh` and `os_updates`. Every run shows its plan first (which servers, in which
batches, what is skipped and why) and asks before it starts. A server that cannot run the action
(too old, above its ceiling, nothing to do) is skipped with the reason. `--serial` sets how many
servers run at a time, `--canary` runs one server alone first and stops everything if it fails,
and `--max-failures` stops the rest after that many failures. Each server runs the action
through its own API, as the same operator, with the same sudo mode and approvals as a call made
there by hand. The fleet pages start the same jobs.

System updates never reboot a server; `noust_update` updates Noust with the server's own
installation method (the package manager or pip).

Updating Noust to a version of your choosing, across the fleet (`noust_update` with
`--option target_version=4.0.0`, or "Update to this version" in the console's dialog): the plan
says each server's step ("Noust 3.3.1 -> 4.0.0"). A server already on that version is skipped, one
whose package source does not offer it yet is skipped with the reason (**"on the way"** when it is
published and its index is behind, `index_behind`), and one that ends up short of it is reported as
failed with the version it is on. `index_refresh` ("Refresh package lists", also in a server's menu
on the fleet's Updates page) reads each server's repositories again so a release published since
is seen; nothing is installed. Without a target, each server takes the newest its own source offers.

A system update that includes the `noust` package restarts that server's console in the middle.
The plan says so ("Also updates Noust"), Noust is installed last (on apt and dnf), and the
central waits for the console to come back and for the job to say how it ended, instead of
counting a failure and skipping the other servers. The job runs in its own systemd unit on the
server, and the console that starts again reads how the unit ended. A server older than 3.2
marks that job interrupted; the central then waits for the server's own record of the update.
In the console, the detail of each server in a fleet job shows the job's log on that server and
what it returned (see [console.md](console-fleet.md#fleet)).

## An application on several servers

An application can run on more than one server: the same repository, deployed to each, behind a load
balancer of yours. The central keeps **a placement** for it - the record "shop.example.com runs on
web-1, web-2 and every server labelled `role=web`" - and nothing more. It holds no copy of the
application: each server keeps its own releases, variables and database, and the central asks them.
It deploys nothing itself and routes no traffic.

```bash
noust fleet placement add shop.example.com --nodes web-1,web-2 --label role=web \
    --serial 1 --canary web-1                          # who runs it, and how an update goes out
noust fleet placement list
noust fleet placement status shop.example.com          # the commit each server serves, who differs
noust fleet placement rollout shop.example.com --plan  # one commit to all, planned first
noust fleet placement rollout shop.example.com --commit 3f9c2ab --serial 25% --max-failures 1
noust fleet placement copy shop.example.com --to web-3 # put it on a server that lacks it
noust fleet placement upstreams shop.example.com --format nginx --port 443
noust fleet placement remove shop.example.com          # forget the record; no server is touched
```

In the console it is **Fleet > Applications > On several servers** (`/api/fleet/placements`, see [api.md](api.md)).

- **Status compares, and says which differs.** Each server answers for itself: the commit it serves,
  when it was deployed, whether the application runs. The commit most servers serve is the reference;
  a server is `current`, `differs`, `missing` (it does not have the application), `unreachable` or
  `unknown`, each with a word and a shape, never only a colour. A server that cannot be asked shows its
  last answer with its age.
- **A rollout ends every server on the same commit.** It is the fleet's `apps_update` with the choices
  an application on several servers needs. The first server goes alone and builds the head of the
  branch (or the commit, tag or image tag you named); **the commit it ends on is fixed for the rest** and
  is kept with the job, so a push that lands halfway reaches none of them and a retry makes the same
  choice. `--serial`, `--canary` and `--max-failures` are the fleet's (above); the placement's own
  values are the default.
- **A rollout that stops puts the servers already moved back**, unless `--no-rollback`. Each server does
  it itself, by activating its previous release behind its own health gate, which is instant. A server
  whose deployment changed the schema of a database says so and is left where it is, because Noust puts
  code back, never a database; an application that deploys in place has no previous release and says
  that. Every server's outcome is in the job (`rolled_back`, or why it was not).
- **An image tag is the way to build once.** For an application that runs a registry image
  (`--type container`), `--image-tag 1.4.2` makes every server pull that tag instead of building the
  same source N times: build and push it from your CI, then roll it out. See
  [What the fleet is not](fleet-model.md#what-the-fleet-is-not).
- **Copy puts an application on a server that lacks it**, from the export of one that has it (the
  servers' own `export` and `import`), and brings it to the commit the source serves. The document, with
  the secrets unless `--no-secrets`, goes from one server to the other through the central's memory and
  is written nowhere: not the job's result, its log or a server's record. `--new-domain` makes the copy
  under another name, to clone an application rather than spread it.
- **The load balancer is yours.** `upstreams` writes the servers for it - an nginx `upstream` block, a
  list of `address:port`, or JSON - marking the ones that serve right now. The addresses are the ones
  each server was added with (a server that dials in has none and is left out, saying so); `--port` is
  the application's port on them.
- **Every step is the server's own API call, as the same operator**, so each server's ceiling applies
  (a `read` server is skipped with the reason), sudo mode and approvals are asked as they are for a call
  made there by hand, and a server older than the action needs is skipped as it is for any bulk action.

## Building on one server and running on others

A small VPS is a poor place to compile an application: a Next.js build wants more memory than the
application ever will. The central can have **one server build it and the others run that build**:

```bash
noust fleet builder set shop.example.com build-1      # which server builds it (the central's note)
noust fleet placement ship shop.example.com --plan    # the plan: who goes first, what is skipped
noust fleet placement ship shop.example.com           # build once on build-1, run it on every member
noust fleet placement rollout shop.example.com        # the same: an update follows the build server
noust fleet placement ship shop.example.com --commit 3f9c2ab --to web-2 --serial 2
noust fleet builder list
noust fleet builder clear shop.example.com            # back to building where it runs
```

In the console it is the **Build on one server, run on the others** and **Choose the build server**
items of an application in Fleet > Applications > On several servers; in the API, `POST
/api/fleet/placements/{domain}/ship` and `/api/fleet/builders` (see [api.md](api.md)).

- **The builder builds once, and nothing else.** It fetches, installs and builds the release in its build
  sandbox exactly as `noust update` would, but runs no hook, no migration, does not switch and records no
  deployment; the release is removed once packed (`--keep` keeps it). The application has to be on the
  builder (its source, variables and build settings) on the release layout, as the same type as on the
  servers that run it: the plan checks that for every server before it builds anything, and says which
  command puts the application on one that lacks it (`noust fleet placement copy`).
- **The central carries the bundle, never holds it.** It is read from the builder by ranges and written
  to each server as an upload that resumes from where it stopped, through the two tunnels, a chunk at a
  time: a request through a tunnel is cut after a few minutes and a bundle is hundreds of megabytes. Nothing
  of it is written on the central. The SHA-256 the builder reports is declared when the upload starts and
  checked when it completes; the server extracts nothing that does not match.
- **The first server goes alone**, like a rollout's: the build is made for it, and the others are sent
  exactly that file, so a push that lands halfway reaches none of them. The bundle is pinned with the job:
  a retry of the failed servers sends it again and does not build again (the builder keeps it until every
  server has it, or for a day).
- **Each server activates it behind its own health gate**, after the application's own `pre_deploy` hooks,
  with its own `.env`, database and persistent paths. A release that does not answer is put back and
  removed, and the job says so with the server's words. When the job stops, the servers already moved are
  put back by their previous release, unless `--no-rollback`; going back past a deployment that changed
  the schema needs `--schema-changed-ok` on the server that would refuse it, because Noust puts code
  back, never a database.
- **The builder is not one of the servers it ships to.** A server that builds and runs the application
  updates the ordinary way; ship to the others.
- **An update follows the builder.** Recording a build server is saying where the application is
  compiled, so `noust fleet placement rollout` (and `POST /api/fleet/placements/{domain}/rollout`, and
  the console's **Roll out**) builds there once and sends the release, exactly as `ship` does; the plan's
  action says `placement_ship`. It does not when the build server cannot build that kind of update, and
  then the plan names the reason and each server builds as it always did: a registry image tag
  (`--image-tag`: the servers pull it, nothing is built), a git tag (`--tag`: the build server builds a
  commit, so give `--commit`), or a build server that is itself one of the placement's servers.
  `--build-on-each` (`build_on_each` over the API) builds on every server although one is recorded.
  Nothing falls back silently: a build server that lacks the application, or is not on the release
  layout, makes the update refuse before anything is built, with the command that puts it right.
- **Every step is the server's own API call, as the same operator**, so each server's ceiling and
  permissions apply (`apps.deploy`; a `read` server is skipped with the reason), a server older than this
  is skipped as `unsupported`, and the builder is judged by the same rule before anything is built.

This is run for real, not only rehearsed, by the integration harness
(`python tests/integration/ship_run.py`): a central and two servers in containers, each a systemd machine
enrolled over the real SSH tunnels and the restricted key; the central ships a build, the release the
target serves says which machine built it while the target's own repository cache never fetched the
commit, an update whose application does not start is refused behind the target's health gate with what
served still serving, and the target goes back to its previous release with one command.

## Moving a database to another server

A database can be moved from one server of the fleet to another in one step, from the central:

```bash
noust db transfer shop_db --from db-1 --to db-2 --plan       # what it would do, nothing else
noust db transfer shop_db --from db-1 --to db-2              # asks, then does it
noust db transfer shop_db -e postgresql@pg17 --from db-1 --to db-2 --to-engine postgresql@pg17 --as shop_copy
noust db transfer shop_db --from db-1 --to db-2 --accounts   # and the accounts that had access to it
noust db transfer shop_db --from db-1 --to db-2 --drop-source --replace
```

In the API it is `POST /api/fleet/databases/transfer` (see [api.md](api.md)), and any client of `POST
/api/fleet/actions` can name the action `database_transfer`. Moving a database between engines of one
server is `noust db move` ([databases.md](databases-move.md#moving-a-database-or-an-engine-to-a-newer-version)).

- **The source dumps, the target loads, the central only carries.** The source takes the dump with its own
  backups (checked and hashed there), the central reads it by ranges and writes it to the target as an upload
  that resumes from where it stopped, through the two tunnels, a chunk at a time, and the target loads it with
  its own import. Nothing of it is written on the central. The SHA-256 the source reports is declared when the
  upload starts and checked when it completes. A dump made for the move is removed from the source when it
  ends, whatever the outcome. The carrying is the same code as a release shipped from a build server.
- **It loads as the database's owner, never as the superuser.** It is the import any dump from elsewhere gets
  on the target (`noust db import`): a dump cannot run programs on the server as root because it came from the
  fleet. `--as-superuser` stays a decision an operator takes on the target, not one the central takes for them.
- **The accounts follow when asked (`--accounts`).** Those with access to the database on the source are made
  on the target with the password the source holds for each and the profile they had, after the data; one the
  source holds no password for is made with a new one the target keeps, and shows with sudo mode (the account's page
  there, or `POST /api/databases/users/{engine}/{username}/password/reveal`).
  The passwords pass through the central in memory and are kept nowhere: not in a log, not in the job's result,
  not in an audit event. The account that owns the database on the source is made first and the target makes the database with it (PostgreSQL), so what the dump holds belongs to the account it belonged to and the other profiles are set against it. The engine's own accounts and those the target already has are left as they are. An
  account that cannot be made fails the server after the others were tried, with the target's words; the data is
  there by then, and the source is not dropped on the strength of half a move.
- **The source keeps the database** unless `--drop-source` is given, and then the source itself drops it, after
  a last dump kept there, and refuses while an application uses it (the move still counts: the database is on
  the target, and the record says the source kept it). The copy is the database at the moment of the dump:
  writes after it are not carried, so stop what writes first when that matters. The applications that use it are
  not pointed at the new server: that is a decision about a network, not about a database.
- **It is checked before anything is dumped:** the source holds the database and offers what moving needs, the
  engine runs on both, the target is not older than the source (a dump of a newer server does not load into an
  older one), it does not hold a database by that name (unless `--replace`, which takes a safety copy of it on
  the target first), and the database is of an engine whose databases travel as a dump (PostgreSQL, MySQL,
  MariaDB, MongoDB; a Redis snapshot is the whole instance). A source whose ceiling does not let this central
  dump and read its dumps (`admin`) is refused with the reason.
- **Every step is the server's own API call, as the same operator**, so each server's ceiling and permissions
  apply, sudo mode is asked once for the job (the target's import marks it), and a server older than this is
  skipped as `unsupported` like any bulk action.

## Settings of the fleet

Some settings are the fleet's: you write them once, on the central, and every server it manages has
them. Five kinds are managed this way: **notification channels**, **alert rules**, **backup
destinations**, **registry logins** and **shared variables** of the server. (Secret managers, DNS and
cloud providers and source integrations are named by the design and are not managed from the central
yet: set them on each server.) What belongs to the machine - the web server, the firewall, SSH,
ports, runtimes, database engines, updates of the system, a server's own console and its security,
accounts and audit - is never pushed; the central shows a difference and the server decides.

A setting has a value for **every server** and, optionally, **exceptions**: a value of its own for
one server, set from the central. A server follows the fleet's value unless it has an exception or is
**independent** (the central pushes it nothing, and it still appears in every fleet view). Whoever
signs in on a server can still change a setting there: the server rules on its own machine, and that
setting becomes **the server's own exception** (`overridden`) until the central is told to put it
back. A server that leaves the fleet, or is made independent, keeps the last value it applied as its
own: nothing a setting put on a server is ever deleted from it.

```bash
# the API, as the console uses it (all under /api/fleet)
GET    /settings                          # the kinds, and every setting in every scope
GET    /settings/status                   # where each server stands on each setting
PUT    /settings/{kind}/{name}            # {"value": {...}, "scope": "fleet" | "node:web-2", "apply": true}
DELETE /settings/{kind}/{name}?scope=...  # the servers keep what they applied
PUT    /servers/{node}/settings-mode      # {"mode": "managed" | "independent"}
POST   /settings/apply                    # "Apply to the fleet": a fleet job, with each server's progress
POST   /servers/{node}/settings/{kind}/{name}/restore   # back to the fleet's value on that server
```

The value of a setting is the body of the server's own route for it (a channel's is what
`PUT /api/notifications/channels/{name}` takes), and the server judges it with its own validators:
the central checks only the shape. A secret goes in and never comes out; it is kept in the central's
secret store (sealed, on a sealed central) and shown as `***`.

**Who approves, and how a server knows.** Setting or removing one needs sudo mode and the permission
the server's own route asks (`settings.manage`, `server.manage`, `backups.manage` or `apps.manage`,
by kind): `fleet.manage` is not enough to manage the registry logins of every server. The change is
recorded with an id and the person who approved it. The pushes that apply it - also the ones made
later, when a server comes back, with nobody in front of the console - carry
`X-Noust-Actor: fleet-sync on behalf of <person> (change <id>)` and `X-Noust-Elevated:
change:<id>`. A server takes that as the confirmation of sudo mode **only** for the routes of these
five kinds and the routes that record what was applied, and **only** when its ceiling is `admin`;
anything else signed that way is refused (`fleet_sync_refused`, `fleet_sync_ceiling`), and its audit
trail names the person and the change.

**When it is applied.** When a setting is saved; when a server answers after a silence, or joins (the
console acts on a note the probe leaves in the store, within half a minute, and again after five if it
did not get done); and when someone presses **Apply to the fleet**. A setting that exists on a server
and is not the fleet's is not taken over unless you say so (`adopt`), and one its operator changed is
left unless you name it (`restore`) or the operator asked for it back on that server. A server on an
older Noust (before 4.1) is skipped as `unsupported`.

## A fleet on different versions

A 3.1 central drives 3.0 servers, and a 3.0 central drives 3.1 servers. A 3.0 server shows as
`unsupported` in the fleet views it does not offer, bulk actions skip it with the reason, and its
ceiling shows as `unknown`. Upgrade the central first, then each server, then move each server to
the tunnel account. See [UPGRADING-3.1.md](UPGRADING-3.1.md#upgrading-a-fleet).

A 3.2 central drives 3.1 servers the same way: the pages that call routes a 3.1 server does not
have (a site's structure and diagram, the time zone list, the timeline, hooks) say that the
server runs an older Noust, and the rest works as before. See
[UPGRADING-3.2.md](UPGRADING-3.2.md#upgrading-a-fleet).

## Notifications

A server is declared down only after it has stayed silent for two minutes (two silences at least
twenty seconds apart), is asked less often the longer it stays down (down to once every six
minutes) and is announced once; it is announced again only after it answered. **Pause a server's monitoring** for maintenance
(`noust node pause web-5 --reason "kernel update"`, `noust node resume web-5`, or **Fleet >
Servers > Pause monitoring**): a paused server is not asked, not counted as down and never
announced, and the fleet still shows what it last answered, with the reason beside it.

A central sends `node_unreachable` when a server stops answering, `node_recovered` when it
answers again, and `node_host_key_changed` when a server's SSH host key changed and the tunnel
was closed, through the central's own notification channels. `server.name` names each server in
every message.

## Updating

```bash
docker compose pull && docker compose up -d        # on UGOS Pro: stop the project, pull the image again, start it
```

The data stays on the volume; the new image reads it where the old one left it. The first
start of a new version prints no token (one was already issued). Pin a version in
`compose.yaml` (`ghcr.io/perkybeet/noust:3.1.0`) to update when you choose, or use `latest`.
Take a `noust central backup` first: a new version may migrate the store, and the copy it keeps
beside the store (`noust.db.v<old>-<time>.bak`) is the only way back to the older image. The
upgrade from 3.0 is described in [UPGRADING-3.1.md](UPGRADING-3.1.md).
