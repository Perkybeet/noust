# Noust 3.2 changelog

Changes since Noust 3.1.17. Upgrade notes are in [UPGRADING-3.2.md](UPGRADING-3.2.md): read them
before upgrading a server that deploys Docker Compose projects or builds as root, and before
upgrading a fleet.

3.2 makes Docker Compose projects first-class (deploy hooks, updates without a cut, adopting a
stack that already runs, a copy of its database before every update) and shows the web server
as a structure and a diagram instead of only as text. It does not change an application until
its operator asks, with the exceptions listed under
[What changes on its own](UPGRADING-3.2.md#what-changes-on-its-own).

## Deploy hooks

Until 3.1 a project could not say what to run around its own deploy, and Prisma's automatic
migration, the one thing that did run, treated a failure as a warning: the new code went live
against the schema it was not written for.

- **`noust.yaml`.** A file at the root of the repository (or `.noust.yaml`) declares
  `hooks.pre_deploy` and `hooks.post_deploy`. Each hook has `run`, and optionally `service`,
  `workdir`, `timeout` (1 to 3600 seconds, 600 by default) and `migrates`. The schema is closed:
  a key Noust does not know, a path that climbs with `..` and a link instead of a file are
  refused, and the error names the field. The same file can say `backup.databases` (see below).
  [compose.md](compose.md#noustyaml) has the whole schema.
- **Never a shell.** `run` is split into an argument list and run through the command runner
  like every other process; a pipe or a redirection goes in a script you commit.
- **Where a hook runs.** In a Compose stack, in a one-off container of the image the deploy just
  built, with the service's own environment and network (`docker compose run --rm --no-deps`),
  before anything is recreated. In every other application that serves, in the release phase of
  the build tree, with the application's identity and its `.env` when it builds in the sandbox,
  and as Noust itself while it still builds as root, as its migrations always did.
  A monorepo runs them at the root of the repository, after the build and before any unit
  restarts. A static site has nothing to run them in, and declaring hooks for one is refused with
  the reason.
- **`pre_deploy` aborts.** The first hook that fails, by exiting non-zero or running out of
  time, stops the deployment before it moves any traffic: what served before keeps serving, and
  the error carries the hook's own output.
- **`post_deploy` warns.** It runs once the new version has passed its health gate. A failure
  leaves the deployment deployed with warnings (recorded as such in the history, with the hook's
  output) and sends the new `deploy_hook_failed` notification, because undoing a version that
  already serves is worse than saying so.
- **The operator's hooks.** `noust app hooks set DOMAIN --file hooks.yaml`, `PUT
  /api/apps/{domain}/hooks` and the application's Settings store a document for one application
  that replaces the repository's whole: they are never merged, so what runs is always one
  document someone wrote. `noust app hooks show` says where the hooks in force come from.
  Writing them needs `root_equivalent`, sudo mode and, when approvals are on, a second person: a
  hook is code that runs with the application's secrets.
- **Prisma's automatic migration aborts on failure.** When a project declares no hooks and
  uses Prisma, `prisma migrate deploy` still runs for Node and monorepo applications, and its
  failure now stops the update exactly as a failing `pre_deploy` does. When hooks are declared,
  the automatic migration does not run: what is written down wins, and the log says so.

## Going back past a migration

- **Each deployment records whether it changed the database's schema.** A hook marked
  `migrates` that succeeded, or Prisma applying at least one migration, marks it. A hook marked
  `migrates` whose output says nothing was applied (in Prisma's, Django's, Laravel's, Knex's,
  Sequelize's, TypeORM's, Doctrine's, Flyway's or golang-migrate's own words) does not.
- **Going back asks first.** `noust rollback`, `noust releases rollback`, `noust update --commit`
  and a restore of only an application's files refuse to go back past a deployment that changed
  the schema, name the deployments, and say what to do: restore the database from the copy taken
  before them, or pass `--schema-changed-ok` to say the older code runs against the schema as it
  is. The API answers `409 schema_changed` with the deployments and takes `schema_changed_ok`,
  and the console shows a dialog that names them. Noust puts code back, never a database.
- **The automatic go-back after a failed health gate still happens**, because the alternative is
  an outage. When the failed attempt had changed the schema, the first line of the error says so
  and the history marks the deployment.
- Deployments in the API and the console carry `schema_changed` and `schema_changed_between`, so
  the warning shows before the button is pressed.

## Docker Compose without a cut

Until 3.1, `docker compose up -d` recreated each web service by stopping its container and
starting a new one: for as long as the new one took to listen, nginx had nobody on the port.

- **A relay.** `noust app zero-downtime DOMAIN on` now accepts a Compose stack. An update starts
  a twin of each web service from the new image on a free loopback port, runs the health gate
  against it, points nginx at it, recreates the real container, runs the gate against that,
  points nginx back, and removes the twin. Services go in `depends_on` order, so a new front end
  never talks to an old back end. In the project's harness, a probe every 200 ms saw 0 failed
  requests of 110 through a relayed update, against 72 of 94 through a plain recreate.
- **A twin that does not start touches nothing.** The image failing to start is the proof, and
  the container that serves is never recreated. A recreated container that does not answer
  leaves the twin serving and the error says which one serves. A twin left by an update that was
  interrupted is resolved first, and `noust setup doctor` reports one it finds.
- **Requirements, checked when it is turned on**, each with its fix: a fixed host port on each
  web service, and a servers file the site reaches it through,
  `/etc/nginx/noust-upstreams/<app>/<service>.servers`, which the site Noust writes includes by
  itself. A site you wrote yourself must include it, and turning the mode on says the exact line.
  See [compose.md](compose.md#zero-downtime-updates-the-relay).

## Adopting a stack that already runs

`noust app adopt DOMAIN --path DIR` (and `POST /api/apps/adopt`, and the new-application page)
registers a stack someone brought up by hand, such as one run by its own `deploy.sh` under
`/opt/<name>`, without touching it.

- Nothing is cloned, cleaned, rebuilt or restarted. The Compose project name is read from the
  running containers' labels and kept; every later command and the unit pass it with `-p`.
- Noust proves it would change nothing: `docker compose up -d --remove-orphans --dry-run
  --no-build` must say every container is already as it would be. Otherwise the adoption is
  refused with Compose's own output, unless you read it and pass `--accept-recreate`.
- The site that serves the domain is found by the names it answers on and left as it is. When
  its file is not named after the domain (`proggest` for `proggest.es`), the file name is
  recorded, and the one place that turns a domain into a site file resolves it.
- The unit is created and enabled, not started. The application is registered in place, at its
  real directory, which can be outside the apps directory.
- Deleting an adopted application never removes its directory unless you name it
  (`--remove-adopted-directory PATH`); its own site and the servers files it includes are kept
  and reported as kept.

## The stack's database, copied before an update

The pre-update backup of a Compose stack held, at best, a copy of a running database's data
files, which nobody can restore. Going back to the previous containers puts the previous code
back and leaves a migration in the data.

- **Detected and dumped.** Services running the official `postgres`, `mysql`, `mariadb` or
  `mongo` image (any tag) are dumped through the engine's own client inside their container,
  into the pre-update backup, compressed and `0600`. The user and database come from the
  service's environment as Compose resolves it; a password reaches the client from the
  container's own environment and is in no command line. Other images (`bitnami/postgresql`,
  `postgis/postgis`) are declared in `noust.yaml` under `backup.databases`.
- **On by default, and a failure stops the update.** Without the copy there is no way back from
  a migration. The error carries the engine's output and the command that turns it off:
  `noust app backup-before-update DOMAIN off`, or `backup.databases: off` in `noust.yaml`.
- **Restore.** `noust backup restore BACKUP_ID --databases-only` puts a stack's databases back
  with the application stopped (`pg_restore --clean --if-exists`, and the other engines'
  equivalents); a full restore does it too when the backup holds them.
- `noust backup create` and the backup manager use the application's real directory instead of
  assuming `<apps directory>/<name>`, so an adopted stack can be backed up, and a stack's Docker
  volumes are found under their real names (project prefix and `name:`).

## Your own site

3.1.17 respected a site you had written for applications deployed through releases. 3.2 does it
for every path that writes or deletes a site.

- **A site without Noust's marker is never rewritten and never deleted.** Compose deploys and
  updates, certificates, rollbacks, monorepos and `noust delete` leave it alone and say so.
  Obtaining a certificate for a Compose application used to delete the site and write a new one.
- What Noust would need it to contain (its names, the servers file include, a certificate) is
  checked and reported as warnings with the exact fix.
- **The list of sites** (`GET /api/sites` and the console) shows every file in the web server's
  directory, not only the store's, each marked as written by Noust or by you, and with the
  application it belongs to. A hand-written site no longer disappears from the list
  as soon as the store has one.
- See [domains.md](domains.md#operator-sites).

## Aliases, redirects and certificates for Compose and monorepo

`noust domain add` refused Compose and monorepo applications. What a deployer does to render its
site, hold a certificate and cover every name now lives in one place, and Docker Compose and
monorepo applications use it: aliases, redirects and one certificate for all the names work for
them too (`monorepo.conf.j2` gained the server names and the redirects). See
[domains.md](domains.md).

## Ports and routes

- **One reader of a Compose file's ports.** The short and long forms, with or without an address
  (IPv4 and `[::1]`), ranges, `/tcp` and `/udp`, and values already resolved. The first-port
  reader failed on `127.0.0.1:3000:3000` and fell back to 3000.
- **The root route.** With several web services and no `noust.nginx.yaml`, `/` goes to the
  service in front (the one that depends on another web service and that no web service depends
  on; the first if there is none). Noust no longer invents a `/<service>` route for the others,
  which never worked for a Next.js front end; they are listed in a warning with how to route
  them.
- **`noust.nginx.yaml`** gains per-route and server-wide `max_body_size`, `buffering: false`,
  `read_timeout` and `send_timeout`, `static` directories served straight from the application
  (with `cache`), and `return` for redirects. The server-wide `rate_limit` now applies to every
  route without its own limit; it used to declare a zone no location used. The schema is closed
  and each value is checked against what its directive takes. See
  [compose.md](compose.md#routing-with-noustnginxyaml).

## Workers without a web port

A Compose stack that publishes no port is not a web application that is down. There is one
definition of it (no service publishes a TCP port on the host), and the deploy, the store,
`noust diagnose`, the monitor and the summaries all use it.

- Such a stack is judged by its containers: well when every one runs or exited with 0 and none
  restarts in a loop, broken when one exits with an error or restarts. `noust diagnose` shows the
  containers and the last lines of `docker compose logs`, not an HTTP probe of a port nothing
  listens on, and does not call the unit's `active (exited)` degraded: that is what a Compose
  unit looks like.
- A stack that an earlier version registered with a port (WASM 1.x recorded the default) is
  pointed out by `noust diagnose` and `noust health` with the fix, `noust app headless DOMAIN`,
  which clears the port and, when Noust wrote its site and no alias uses it, offers to remove
  the site. Nothing does it by itself. `GET|POST /api/apps/{domain}/headless` is the same.

## Deploying by tag

- `noust create --follow-tags 'v*'` and `noust app follow-tags DOMAIN 'v*'` make an application
  follow the tags that match a glob instead of a branch. They are ordered by version, and never
  a tag older than the one deployed (it is ignored and recorded).
- The webhook accepts, for those applications, a published GitHub `release` (not a prerelease or
  a draft) and a push of a tag; a push to a branch deploys nothing. The deployment is the exact
  commit the tag points to.
- `noust update DOMAIN --tag v1.2.3` deploys one tag. `noust app follow-tags DOMAIN --off`
  goes back to a branch.

## The web server, at a glance

- **A parser that loses nothing.** `noust.managers.siteconf` reads an nginx or Apache site into
  a tree that renders back byte for byte: comments, spacing and order included. Text it cannot
  read is an error with its line and column, never half a tree. It builds the model the console
  shows (servers with their listeners and certificates, locations with their targets, upstreams,
  includes, comments) and keeps every directive it does not model as raw, visible and editable.
  Edit operations change only the bytes of the element they name and keep the surrounding
  indentation. A routing function applies the web server's own algorithm to say which server and
  location answer a request, step by step.
- **The API.** `GET` and `POST /api/sites/{domain}/structure`, `POST .../config/edit`, `POST
  .../route` and `GET .../topology` (who owns each port, whether it answers, when the certificate
  expires). They write nothing: saving a site is still the one `PUT .../config` (root-equivalent,
  sudo mode, four-eyes, tested before it is written).
- **The site page has three views**, Text, Structure and Diagram, sharing one draft and one save
  bar. Structure edits a site visually (locations in the order the web server evaluates them,
  inline settings, adding, duplicating, moving and removing locations from templates). Diagram
  draws how a request travels, from the ports through the names and locations to the backends
  and who holds each backend's port and whether it answers, with the flow animated by CSS and
  still under `prefers-reduced-motion`. **Try a URL** animates the path one request takes and
  explains each step of the choice. See [console.md](console.md#a-site-as-text-structure-and-diagram).
- **A site's configuration is tested inside the live one.** The test used to wrap the site in an
  empty `http` block, so a site that used a `limit_req_zone`, a `map` or a `log_format` declared
  in `nginx.conf` failed while valid, and one that declared an `upstream` another site already
  declares passed and then broke the reload. Now the test copies the real main configuration
  with the enabled sites listed, this one replaced by the candidate. Apache is tested the same
  way.

## Builds and accounts

- **The build sandbox becomes the default for applications from before 3.1.** On the next update
  of an application that still builds as root, Noust first builds that exact commit in the
  sandbox, as `noust app sandbox test` does. If it passes, the sandbox is turned on and the update
  builds in it. If it fails, the update builds as root as it always did, the reason is recorded,
  and the application page, `noust health`, `noust ens check` and a notification (under
  `deploy_failed`) say so. Noust tries once; `noust app sandbox test` retries. An application
  with `noust app sandbox disable --reason` is left alone, and so is one on a server where the
  sandbox does not hold. An update that worked never breaks because of it.
- **Each application its own account.** An application created from 3.2 runs as
  `noust-app-<name>`, a system account with its own group, no home and no shell, that owns its
  tree, `.env` and caches; a PHP application's pool runs as it. One compromised application can no
  longer read or write another's tree. Existing applications keep the shared account until you
  move them: `noust app identity status|migrate DOMAIN` tests the change behind the health gate
  and puts everything back exactly if the application does not answer. Docker Compose stacks,
  monorepos and static sites do not get one.

## Monitor, notifications and the timeline

- **Notifications when an application stops answering.** The monitor asks every application
  that runs a process what its deploy's health gate asks and sends `app_unreachable` after three failures in a row
  over at least a minute, once per outage, and `app_recovered` at the first success. A restart or a
  deploy never reports; nothing is probed while a deploy, update, rollback or job of that
  application runs, and an application stopped on purpose does not report. A stack with no
  published port is judged by its containers. Both are on by default. Until now only `unit_failed`
  existed, which fires when systemd gives a unit up, so a web with its unit running that no
  longer answered notified nobody.
- **`deploy_hook_failed`** notifies a deployment that went live with warnings.
- **Process samples.** Every minute the monitor keeps the five processes using most CPU and the
  five using most memory (pid, name, who owns it: a Noust application, a Compose service or a
  unit, the user, CPU, memory and a truncated command line with its secrets redacted) in the
  metrics database, for 26 hours, the retention of the per-minute metrics. History starts one
  minute after the monitor starts.
- **One timeline for a stretch of time.** `GET /api/timeline` merges the journal (warnings and
  worse), the audit trail, deployments, jobs, what the monitor saw and the process samples for a
  start and an end, optionally narrowed to one application. Each source keeps the permission it
  already had, and one that you may not read is named as withheld, not silently dropped.
- **Investigate this stretch.** On an enlarged chart, a selection (or the visible range) opens
  that timeline in a panel, which answers "why this peak" with the processes that were running.
  Through a central it works on any server.

## Jobs and the fleet

- **A job that runs in a transient systemd unit survives a console restart.** An operating
  system update and Noust updating itself run in a unit of their own and record it; when the
  console restarts, it reads how the unit ended instead of marking the job interrupted. Ended well: the job completes with the unit's output. Failed: it fails with the
  unit's words. Still running: it keeps following it. A job with no recorded unit is marked
  interrupted as before. This is why an `apt upgrade` that included the `noust` package, whose
  restart of the console used to mark the job failed, now reads as it ended.
- **A fleet's operating system update that includes Noust** says so in the plan, installs Noust
  last (on apt and dnf, which can leave a package for a second command), and waits for the
  node's console to come back and for the job to say how it ended, instead of counting the node
  as failed and skipping the others. A node older than 3.2 marks the
  job interrupted; the central then waits for the node's own record of the update.
- **The server detail of a fleet job shows what happened.** The drawer for a server in a fleet
  job shows the job's log on the node, live while it runs, with the result in words (packages
  installed, versions, certificates renewed), a link to the job on that server, and "each
  application" only for the actions that are per application.
- **Measuring storage is a read.** `POST /api/server/storage/analyze` needs `server.read` (it was
  `server.manage`), so a central with the `read` ceiling can ask what takes the space. A refusal
  by a ceiling now names the lowest level that would allow the call.

## The console

- **A time zone selector.** Server > System > Change time zone is a searchable list: by name,
  city, abbreviation or offset (`madrid`, `+2`, `utc+1`), grouped by region, each zone with its
  offset now and abbreviation, and the time the server will have with it. The list comes from
  the managed server (`GET /api/server/clock/timezones`; on a fleet, from the node) without
  the legacy aliases (`posix/`, `right/`, `SystemV/`, `EST5EDT`).
- **Activated or not, at a glance.** Each page whose feature can be on or off (notifications,
  instant rollback, zero-downtime, automatic updates, builds, previews, scheduled backups, two-
  factor, approvals) shows the state as one header with a colour, a shape and a word: on, off
  with what that means and the action to turn it on beside it, or on with a problem (amber).
- **Processes.** "Show 50" works (the query was cached without its limit), the order is chosen by
  clicking a column header, ordered on the server, and the button says how many there are.
- **Charts in a group.** Pointing at one chart shows the moment on the others as a crosshair
  only; a click no longer freezes them all until the next one; the selection you drag is a
  translucent tint that leaves the readings visible.
- **Application settings** gain the hooks, the tags an application follows, zero-downtime for a
  Compose stack, the database copy before an update, the account it runs as and, in the new
  application page, adopting a stack.
- **Spanish `.env` placeholders.** `genera-clave-hex-64-caracteres`, `cambiar`, `tu-` and
  `pon-` in an `.env.example` are placeholders like their English cousins: never written verbatim,
  and a secret that a name says is hexadecimal is generated as hexadecimal of the length the name
  says.

## Fixes

- **Deleting a Docker image from Storage answered 500** and left the image deleted without an
  audit record, with the console saying nothing had changed. The image id travelled as `target`
  next to the audit record's own, which raised a `TypeError` after the job was queued. The audit
  is recorded first, as `image`.
- **A hook marked `migrates` marked every deployment as changing the schema** when it succeeded,
  so each update of a project running `prisma migrate deploy` counted, and a failed update said
  the database had been changed when nothing was applied. Found by running Proggest in the
  harness; the output is now read in the migration tools' words.
- **`noust --dry-run app adopt`** refused every checkout: reading its origin was not a declared
  read-only probe.
- **`noust create --force -t docker-compose`** no longer empties a directory that holds data:
  it brings a git checkout that is already there up to date in place and says what it keeps.
  Without `-t`, automatic type detection still fetches before the Compose deployer is chosen, so
  name the type when the directory holds data.
- **A Compose stack's volumes** are found under the names Docker gave them (project prefix and
  `name:`); a backup used to look for one that did not exist and mount an empty one.
- **Docker Compose in the integration harness.** The Compose scenario had never run: with no
  Docker in the container it printed a note and counted as a pass. It now skips explicitly, the
  harness image carries Docker, and the scenarios above, and a clone of Proggest, run for real.

## Packaging

- **The store moves to schema v13, one way.** A copy of the store as it was is written beside it
  first (`noust.db.v12-<time>-<pid>-<id>.bak`); 3.1 refuses to open a store written by a newer
  version, and going back to 3.1 means restoring that copy. See
  [UPGRADING-3.2.md](UPGRADING-3.2.md#rolling-back). v13 adds the operator's hooks
  (`app_hooks`), what each deployment ran and whether it changed the schema, the Compose project,
  site name, tag pattern, database-copy setting and account of an application, when the sandbox
  was tried by itself, and the unit a job runs in.
- No runtime dependency is new, in Python or in the console.
- New paths: `/etc/nginx/noust-upstreams/<app>/<service>.servers` (the servers files; WASM's
  `/etc/nginx/wasm-upstreams` is still read where it exists) and the `noust-app-<name>` accounts.
- The man page and the completions cover the new commands.
- The package documentation adds [compose.md](compose.md) and
  [UPGRADING-3.2.md](UPGRADING-3.2.md).
