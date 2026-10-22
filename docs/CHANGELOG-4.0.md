# Noust 4.0 changelog

Changes since Noust 3.3.1. Upgrade notes are in [UPGRADING-4.0.md](UPGRADING-4.0.md); what a client of
the HTTP API can notice is in [API-CHANGELOG.md](API-CHANGELOG.md).

4.0 adds teams, projects and environments, applications built from a Dockerfile, a registry image or
Railpack, a catalogue of 143 recipes, database engines that Noust runs in containers beside the host's,
a certificate of its own for any name of an application, per-site access control, single sign-on
through four kinds of identity provider, servers created at a cloud provider and servers behind NAT,
a one-command installer, and a console rebuilt around setting rows and one set of rules. It is a major
version because it breaks compatibility in two places. It stops supporting the 2.x line: 4.0 no longer
moves a server from the names the product had before 3.0, so a server still on WASM 2.x goes through
3.3.1 first. And it retires those names everywhere else: the `wasm` command and the packages that
brought `noust` in are gone, and the cookies, headers, units, accounts, folders and files that still
spelled `wasm` now spell `noust`, with the old spellings accepted on input until 5.0 where a client or
a repository may still use them.

## Before you upgrade

[UPGRADING-4.0.md](UPGRADING-4.0.md) has the whole guide; this is what to know first (item 84).

- **From WASM 2.x, update to 3.3.x first.** 3.3.1 moves the server onto Noust's names the first time
  root runs a command, and 4.0 does not. On a server whose configuration or store is still a real
  directory under the old names, 4.0 refuses every command except help and version and says how to
  go on, and `apt`, `dnf` and `zypper` stop the installation before unpacking anything.
- **Sign in to the console again, once.** The cookies are `noust_session` and `noust_csrf`, and the
  header the console echoes is `X-Noust-CSRF`. The cookies of 3.x are never read and signing in or out
  expires them.
- **Scripts and cron jobs change.** `wasm` is not a command any more: use `noust` with the same
  arguments. `WASM_*` variables are not read: use `NOUST_*`. A script that names `/etc/wasm`,
  `/var/lib/wasm`, `/var/backups/wasm`, `/etc/nginx/wasm-upstreams` or `~/.local/share/wasm` must
  change before the update, because the package removes those links when nothing uses them. A script that
  reads `noust db list --json` reads `app`: the `linked_app` key, which repeated it under the name 2.x
  used, is gone.
- **Some security friction is a choice now (item 83).** On the standard profile, which is the default,
  the administrator holds every permission, a person may hold accounts of several roles, and a new account
  is not held at the second-factor enrolment unless the console is published. A server that is on
  `security.profile: ens-medium` keeps all of it. [UPGRADING-4.0.md](UPGRADING-4.0.md#what-else-changes-on-its-own)
  says how to get the stricter behaviour back.
- **What still works until 5.0.** The `X-WASM-CSRF` header, the WebSocket subprotocols `wasm.auth` and
  `wasm.token.`, `wasm.nginx.yaml` in a repository, the `?wasm_only=` filter of `GET /api/services` and
  API tokens with the old prefix (until each is revoked or expires). Backups and exports of 3.x are
  read for as long as the file exists. An nginx site you wrote that names `wasm_bg_<app>` keeps working:
  Noust defines the old upstream beside the new one and never edits your site. `noust health` names the
  tokens, repositories and sites that still use an old name, `noust setup doctor` prints the file, the
  line and what to write for a site, and `noust setup rename` finishes the renaming without restarting
  anything.
- **Some things are renamed by the next thing that touches them**: the unit of an application
  deployed before 0.14.1 at its next update, a database's read-only account (`noust_ro_<database>`) at
  its next read, the files Noust keeps in an application's tree at its next deployment, and the first
  line of sites and units written before 3.0 on the first privileged command (with copies under
  `/var/backups/noust/names-4.0`). Backups go to `noust-backups` from the next backup; the old folder
  stays listed, restorable and under retention.
- **Update the nodes of a fleet first**, as before. A central of 4.0 and a node of 3.3.1 work together
  in both directions: the one document that carried an old name, an application's export, is written in
  the spelling the destination's release reads.
- **The store moves to schema 15.** An upgrade keeps a copy of the store beside it, and 4.0 seals the
  credentials of notification channels in `config.yaml`, which an earlier release cannot read.
  [downgrading.md](downgrading.md) says how to go back.

## Applications and deployment

### Building and deploying

- **Builds wait their turn.** A push or a deploy that arrives while an application builds is queued
  instead of refused as busy. Builds of different applications overlap up to
  `deploy.max_concurrent_builds` (2 by default), one application builds one thing at a time, the queue
  holds `deploy.queue_limit` requests (25 by default, then a 429), and requests that ask for the same
  thing merge into the update that waits. `noust deploy queue` lists what runs and what waits.
- **A deployment can be cancelled.** Until its release starts to be activated, a deployment can be
  stopped from the queue, `noust deploy cancel` or the console: the half-built release is removed,
  whatever served before is never touched, and the row says it was cancelled and by whom. A cancelled
  deployment reads as cancelled, not as a failure, in the applications list, the overview, the timeline,
  the fleet views and the command palette. A job that cannot be cancelled says why
  (`cancel_blocked_reason`). Compose, container and monorepo applications are cancellable too.
- **Zero downtime is the default for new applications.** A new application on the releases layout that
  runs a process behind nginx starts in zero-downtime mode once its first deployment answered. An
  existing application keeps restarting in place until its operator turns it on.
- **A build has a time limit.** `deploy.max_minutes` (60 by default; 0 is no limit) stops a build that
  runs past it, and the deployment fails saying why. An application can also set how long its install
  and its build may take.
- **An update is refused when the backup it needs did not happen.** An application with a database
  requires a backup before its update (`noust app backup-required DOMAIN on|off|default`): a backup that
  fails stops the update before anything is pulled or built. Without a database a failed backup stays a
  warning, and going back is never held to a backup of the bad state.
- **Deploy history, pauses and freezes.** An application keeps the history you choose, can pause what
  deploys by itself after a rollback, and deploys can be frozen for a stretch. An application can
  update itself on a schedule, with a backup first, held by a pause or a freeze. A release can be built
  without serving it and made to serve later without building again. `noust app tag deploy TAG` updates
  every application that carries a tag, and `POST /api/jobs/update-many` does the same for a team, a
  project, an environment or a tag in one call.
- **The deployment log is readable.** Filter it by level, search it, hide the times or copy a version
  that is safe to share; on a failed deployment the log opens on its first error.
- **Going back is one question.** The version, what goes back (a switch, a rebuild, a snapshot or a
  backup), what stays, whether automatic deploys pause, and the schema-change warning are asked once,
  from the header, a row, a deployment's page, Diagnose or the palette. Going back to a release brings
  back the processes it declared. A Compose stack keeps the images of each release it served and goes
  back to one without building.

### What gets built

- **Build block and pinned runtimes.** An application declares its commands, base directory, publish
  directory and runtime in settings or in `noust.yaml`, and pins the Node, Python or PHP it runs on;
  Noust installs and verifies the version. Nuxt, SvelteKit, Astro, Remix and Angular are recognised
  (see [frameworks.md](frameworks.md)).
- **More languages, built on the server.** Go, Rust and Ruby on Rails, Java and Kotlin, .NET, Elixir
  and Deno are built in the build sandbox, and each was built and served for real in containers. A Rails
  application from 7.1, which carries a Dockerfile, deploys as a container unless it is created with
  `-t ruby`. System packages an application needs can be declared and are installed for it. A PHP
  application can be served through Apache as well as nginx, with the same password, address lists,
  prefixes and headers. See [build.md](build.md).
- **Process types and workers.** A `Procfile` or `noust.yaml` declares process types. The processes a
  repository declares run beside the application from the same release, are restarted and judged with it
  by the health gate, and can run in as many copies as needed. A web process can run as several copies
  on one server with nginx sharing the traffic. A new application is deployed from its Procfile.
  See [runtime.md](runtime.md).
- **A startup check you can describe.** The check can be a command or a request with its own method,
  scheme, host, port, interval, retries and start period.
- **Pending changes.** An application's page lists what it is set to that it does not run with yet and
  what applying each takes, never showing the values of secrets.
- **Unit hardening and tuning.** A new application's unit runs in systemd's sandbox from its first
  deployment (profiles off, default and strict); an existing one is never hardened by a redeploy, and
  `noust app hardening set` moves it into the sandbox behind the health gate. A soft memory limit, swap,
  CPU and disk weights, pinned CPUs, ulimits, capabilities and devices can be tuned beyond the hard
  limits. See [runtime.md](runtime.md#tuning-the-unit).
- **Files.** Files kept for an application are linked into every release, server paths can be bound
  into a container, and a file browser lists, opens, downloads, uploads, edits, moves and deletes an
  application's files as its own account.
- **Disk.** Noust says what each application takes of the disk by place, holds new releases to a limit
  you set, asks for room before every update fetches anything, and can give the limit to the kernel as a
  project quota on XFS or ext4 mounted with `prjquota`.
- **Scheduled tasks** can be given the longest they may run and can run in a service of the
  application's Compose stack. `GET /api/cron/{name}/ledger` says which occurrences ran, were late or
  never ran.
- **Adopting a site that already runs.** `noust app adopt-site` registers a site on nginx or Apache
  without rebuilding it ([adopting.md](adopting.md)).

### Variables and the environment

- **Shared variables by scope.** Variables belong to a team, a project, an environment or the server,
  and a value can refer to another with `{{team.NAME}}`, `{{project.NAME}}`, `{{environment.NAME}}`,
  `{{server.NAME}}` or `{{app.URL}}`, or to a secret manager with `{{secret:PROVIDER/PATH}}`. Secrets are
  resolved at each deploy ([variables.md](variables.md)).
- **Who gets each variable.** A variable is for the build, the runtime or both, and Noust adds
  predefined variables that say what it knows about the deployment. Compose, container and monorepo
  applications decide their variables in the same place as every other type.
- **The `.env` as a file.** It can be viewed and downloaded (values masked unless an administrator in
  sudo mode asks), and a real `.env` can be brought in. Values may have several lines and a comment above
  each variable. Changing one variable, or who gets it, no longer needs the whole environment.
- **Deleting an application says what goes.** Volumes and databases go only if you ask, and the
  application's name is typed to confirm.

### Sources, previews and environments

- **More ways to fetch code.** GitLab by token or by signing in, GitHub Enterprise Server and Cloud,
  Bitbucket Cloud (webhooks deploy and preview), any SSH user and port, submodules, Git LFS, shallow
  clones, skip markers and watch paths. A repository can have an SSH deploy key of its own, generated or
  imported and rotated without a gap, and a code host's key can be pinned before the first connection.
  See [sources.md](sources.md).
- **`git push` to deploy.** An application can be deployed with `git push` to a repository on the
  server, from an archive picked in the browser, or from a Dockerfile pasted in the console. An
  application can be pointed at another repository without creating it again.
- **Commit statuses and comments.** A deployment marks its commit, and a preview comments on its pull
  request, on GitLab, Gitea, Forgejo and Bitbucket. Forgejo and Gogs deliver webhooks. Gitea and Forgejo
  were run for real: both also send `X-Hub-Signature-256`, and the receiver no longer takes their
  deliveries for GitHub's.
- **From a pipeline.** A deploy endpoint takes a token, and there is a GitHub Action ([ci.md](ci.md)).
- **Monorepos.** A JavaScript monorepo deploys as one application per workspace, can run on releases
  with instant rollback, and its previews build only what a pull request changes
  ([workspaces.md](workspaces.md)).
- **Previews** exist for Docker Compose applications, can be made by hand from a pull request, a branch
  or a tag, are named by a template, are never indexed by search engines, can be given a copy of the
  data and lifecycle hooks, and a fork's pull request waits for an operator to approve it commit by
  commit. The DNS of their names is checked before one is built ([previews.md](previews.md)).
- **Environments and tags.** A release can be promoted to the same application in another environment
  without building it again, an application can be cloned, and applications, databases and services
  carry tags: everything that carries one can be restarted together and applications updated together.
- **Bulk actions.** Applications can be chosen in the list and acted on together, with the plan read
  first and each outcome in the server's words.
- **Declaring a server.** An application can be written as a declaration file, reviewed block by block
  and applied. A manifest writes a server down, previews what applying it would change and applies it,
  including its cron jobs and, on a central, its fleet ([manifest.md](manifest.md),
  [declarative.md](declarative.md)).

## Compose and containers

- **Container applications.** `--type container` runs a Dockerfile, an image from a registry or a
  Railpack build as a Compose project Noust writes itself: one image per release, published on
  `127.0.0.1` only, behind the same unit, web server and health gate. Going back writes that release's
  Compose file again. A repository with only a Dockerfile is proposed as a container
  ([containers.md](containers.md)).
- **Images that follow a tag.** An application that runs a registry image can follow its tag, update
  when Docker Hub or GHCR says the tag moved, and run the command and entrypoint it is given. An empty
  command is refused, because Docker cannot run an image's entrypoint without its command. Registry
  accounts for private images are kept, tested and chosen per application.
- **A deployed service's Compose edited in place.** The document of a stack deployed from one can be
  read, judged and replaced without deleting the stack (`noust app compose document`); a stack from a
  repository, adopted or from a recipe is refused with the place where it is edited.
- **A Compose document is read the way Compose reads it**, so an unquoted `2222:22` is a port and
  `restart: no` is `no`. A document typed into the console is looked at, then deployed like any stack.
  The file, profiles and extra arguments of a stack can be chosen after it exists.
- **Required variables.** Noust says which variables the Compose file asks for and whether the stack
  has each, and refuses to build when a required one has no value.
- **Images are downloaded before the unit starts**, and the download can be stopped like a build. A
  large image no longer fails the first install with "Command timed out after 120s".
- **Logs.** A stack's logs can be read one service at a time, and the Logs tab keeps the journal as one
  choice.
- **One registry of ports.** An application, a published stream, a database made reachable, an engine
  Noust runs, the console, SSH, the web server and what a Compose stack publishes in `ports:` are
  checked by the same code, before anything is built. A stack that publishes 8080 now meets the
  console's port. A stack is given no port of its own unless one is asked for, and a worker the restart
  policy keeps bringing back is not reported healthy.
- **Stacks Coolify or Portainer made** can be adopted, and an update pulls their images. See
  [coolify.md](coolify.md) for taking over a server Coolify ran: `noust update` writes `NOUST_*` into the
  stack's `.env` and recreates the services that read it, keeping their volumes.

## Databases

- **Engines in containers (item 79).** Beside the host's PostgreSQL, MySQL, Redis and MongoDB, Noust can
  run another version of an engine, or one the host lacks, as an instance: the engine's official image
  pinned by tag and digest, published on `127.0.0.1` only, held to a memory limit, with its data in a
  volume and a unit that brings it back after a reboot. An instance is a key such as `postgresql@pg16`
  wherever an engine name went, so the explorer, accounts, dumps, policies, restores and imports drive it
  with the code that drives the host's engines. `noust db instance` and `/api/databases/instances` make,
  start, stop, upgrade and remove them; an upgrade dumps every database first and puts the previous image
  back when the engine does not answer. An instance can also run an image, command or arguments of your
  own, once the price of leaving the catalogue is said and accepted.
- **More engines and sources.** KeyDB and ClickHouse run as instances (both started for real), MySQL can
  be installed from Oracle's repository, and a SQLite file of an application is listed, checked and
  copied whole while in use. PostgreSQL extensions can be loaded, and an instance can run PostGIS or
  pgvector. An instance can run SQL scripts the first time it starts and has memory, CPU, swap and probe
  settings changed behind the readiness gate. An engine on the host can be held to memory, processors,
  swap and tasks.
- **More distributions.** Engines install on Rocky Linux, AlmaLinux, RHEL and openSUSE Leap, each proved
  in a container of that release ([databases.md](databases.md)).
- **Choosing at creation.** A new application's database is created on the engine and version you
  choose, on an instance that runs the version if the host does not, and a recipe that offers several
  SQL engines lets you choose in the CLI, the API and the wizard.
- **Public access, on purpose.** A database can be made reachable from the network and made private
  again. Importing a dump from another provider, a whole `pg_dumpall` (with accounts and privileges if
  asked) and a `mysqldump` of every database come in whole. A plain dump from outside is loaded as the
  database's owner, never as the superuser, unless you ask with `--as-superuser`.
- **Moving data.** A database moves to another engine and an engine to a newer major version, with a way
  back (`noust db move`, `noust db instance migrate`). A database moves to another server of the fleet in
  one step, with its accounts if asked (`noust db transfer`).
- **The host's PostgreSQL to any instant.** `noust db pitr enable` archives the write-ahead log and takes
  base backups, sending both to the backup destinations; `noust db pitr restore --time ...` recovers the
  whole cluster to that instant, keeping the data directory it replaces and putting it back if the
  recovery cannot finish. It uses the engine's own mechanism, so a server that loses Noust recovers by
  hand from the same files.
- **TLS and replicas.** Instances can serve TLS with a certificate of Noust's own authority, and so can
  PostgreSQL, MySQL and MariaDB on the host, renewed without a restart (`noust db tls`); Redis, Valkey and
  KeyDB in an instance serve TLS on their one port. PostgreSQL read replicas on another server run over TLS
  with an account that can only replicate, and MySQL and MariaDB replicas over verified TLS
  (`noust db replica`). The console turns an instance's TLS on and renews it from the instance's menu; the
  replicas and the host engines' TLS are driven from the command line and the API for now.
- **Backup policies.** One policy covers every database of an engine with exclusions, can keep dumps by
  size as well as by number and age (here and at each destination), and can keep no copy on the server
  once every destination holds the dump. A database's users and every database of a cluster come in with
  an import.
- **Safer dumps.** A dump that exits cleanly and wrote nothing is refused and removed, a dump that
  grants to the read-only console's role loads on a server that lacks the role, and a gzipped dump is
  checked on a server that never took one.

Limits that are written down: MySQL and MariaDB are loaded with the administrator account; their
`caching_sha2` hashes do not travel; Redis, Valkey and KeyDB support TLS `required` only; and ClickHouse
has no TLS.

## Backups and recovery

- **More than databases.** Scheduled backups cover an application's data directories and Docker volumes,
  with retention, destinations and a restore that keeps a safety copy. Noust's own state is one encrypted
  bundle, taken on a schedule, proven restorable and put back on a fresh install. An application's
  backups, its data and Noust's own state can be kept only on the destinations, and they can be kept by
  size as well as by number and age.
- **A whole server comes back.** `noust restore-server` brings a server back from the bundle application
  by application. With `--install` it also installs the web server, Docker and the engines, creates the
  PostgreSQL roles, brings back the databases no application owns, and can be run from the console. The
  roles of a PostgreSQL server travel with its copies, and the web server's configuration travels in the
  bundle. `noust server migrate` prepares a move to another server and checks it once it has happened.
- **Compose databases are dumped nightly.** The databases inside a Compose stack are dumped every night
  and each dump is loaded into a throwaway container of its own image to prove it restores.
- **A restore drill** proves an application's newest backup comes back without bringing it back. It does
  not start the application: the copy would run with the `.env` of the backup, which points at the real
  databases ([disaster-recovery.md](disaster-recovery.md)).
- **Destinations.** Credentials and the key a backup is encrypted with are encrypted on the server's disk
  under a key of its own. A new destination is encrypted unless you say otherwise, and
  `noust backup encryption seal-existing` seals backups made in clear. The copies kept on the server's
  disk are sealed and one door opens them. Four more destinations were added with a guide for each
  provider; the guide lists which were tried against a real server, with the date.
- **A destination is tried before it is kept.** An object store's bucket is checked and never created,
  and the console refuses a destination that does not answer in the server's own words, with "Keep it
  anyway" for the operator who means it.
- **Time, order and noise.** A schedule can have a time limit and a time zone of its own. Every backup
  runs behind the applications with a nice value, a disk class and an optional CPU ceiling
  (`noust backup priority`). A sweep of its own prunes every policy each night, and retention no longer
  waits for a run that went well.
- **Failures keep everything the tool said.** The notice of a failed backup names a record with the
  whole output (`noust backup failures show`), and a backup that stopped or a destination that went away
  is announced.
- **Restores on a new machine.** A restore on a machine that lacks the application's own account makes
  the account first, and a blue/green application's upstream file is written before its site.
- **Deleting an application** removes the timers and policies that back it up.

## Domains and certificates

### A certificate for one name

- **Own certificate per name.** Any name of an application except its primary and its canonical pair can
  have a certificate of its own instead of the one the primary shares: `noust domain add APP NAME
  --own-certificate`, `PUT /api/apps/{domain}/domains/{name}/certificate` or the name's menu in the
  console. The name gets a server block in the site and a certbot lineage named after it (a wildcard is
  `_wildcard.example.com`), renewed and reloaded with the others. Use it to keep a name out of the shared
  certificate, to prove a name over DNS that port 80 cannot reach, or to separate a customer's name.
  `noust domain list` and `noust cert list` say which certificate serves each name.
- **Nothing is cut.** The certificate is ordered while the site still serves as before, then the site is
  rewritten and tested before nginx reloads, then the shared certificate is ordered again without the
  name. A refusal stops at the first step with certbot's words and changes nothing. Giving the name back
  to the shared certificate runs in the opposite order. Each name proves itself over HTTP-01 or DNS-01,
  copied from the application's choice unless `--dns ACCOUNT` or `--http` says otherwise.
- **What it does not do yet.** A site you wrote is not rewritten: the lineage is ordered and Noust says
  which files your site must present. An application's export, its import and a move to another server
  do not carry the choice, and a monorepo cannot use it. The nginx and Apache templates both render it;
  it was run for real against an ACME test server with nginx, over HTTP-01, DNS-01 and a wildcard, and
  not yet with Apache or with a site you wrote. See [certificates.md](certificates.md).

### Domains, DNS and authorities

- **Domains.** A name can be a wildcard alias, can be proxied to another port of the application, and the
  `www` and bare pair can have one served and one redirecting. A name another site already answers on is
  refused unless `--force` says it is meant. The DNS check asks public resolvers as the certificate
  authority will and recognises Cloudflare, Fastly, Bunny CDN and ArvanCloud. A name can be switched off
  without deleting it. Removing a name orders the certificate again without it.
- **A temporary address.** `noust domain temporary` gives an application an address made from the
  server's public address on `sslip.io`, with a certificate, to try an application before a real name
  points here ([domains.md](domains.md)).
- **DNS accounts.** Cloudflare, DigitalOcean, Hetzner, Gandi and Route 53 accounts keep the records of a
  domain. A record that was there is adopted, a different one in the way is a conflict you confirm by
  naming it, and Noust deletes only a record that still carries its marker. The same accounts answer the
  DNS-01 challenge.
- **Authorities.** Choose the certificate authority, bring your own certificate, or prove a name over
  DNS. A DNS renewal reaches the site, the first DNS certificate turns TLS on, and a renewal waits for
  nobody.
- **A private authority for private names.** `noust cert ca init` makes an authority whose name
  constraints let it sign only private suffixes and ranges; `noust cert use APP --internal` serves an
  application with a certificate it signed for every name it answers on. Leaves are renewed thirty days
  before they expire, and Noust tells you how to trust the root on each kind of device.
- **HTTP as well.** An application can be left answering over HTTP as well as HTTPS (nginx only).
- **Unknown names get an answer.** `noust site unknown-hosts set close|refuse|redirect` writes a default
  site that answers a name nobody serves, instead of the first site the web server loaded.

## Network and observability

### Access to a site

- **Per-site access control.** A site can ask for a password, allow or refuse addresses, believe the
  visitor address Cloudflare or another proxy forwards, limit requests and connections per client, send
  chosen response headers and hide default ones. A prefix of a site can have its own password rule,
  headers, service or redirect. nginx and Apache render it from the same policy, and a list that would
  refuse your own address needs confirmation ([network.md](network.md)).
- **Client certificates, a firewall and more.** A site can answer only a visitor who presents a
  certificate its own authority signed; ModSecurity with the OWASP Core Rule Set runs in detect or block
  mode per site; a site can answer only a tailnet or a WireGuard interface; HTTP/3 and forward-auth (with
  presets for oauth2-proxy, authentik and Authelia) are available. The history of a site's file keeps a
  diff and a restore. These were run against real nginx, ModSecurity and openssl.
- **fail2ban.** Run against a real fail2ban, the SSH and nginx jails now ban: a jail keeps its ban action
  after a reload, a proxy you declare later is not banned at its first wrong password, a site created
  after the jails started is read, and a site protected by a password answers.
- **TCP and UDP.** nginx can publish TCP and UDP ports, and an application can be reached through a
  Cloudflare Tunnel.

### Watching the server

- **The web server is watched.** The monitor announces a crash of nginx or Apache, runs its
  configuration test, restarts it within a limit an hour and leaves alone a server a person stopped
  (`monitor.restart_webserver` turns it off).
- **Containers that keep restarting** are announced with their exit code and last lines, and what Docker
  did to a container (a death, an out-of-memory kill, a restart, a failed health check) is kept beside
  the metrics and shown on the timeline.
- **Security releases and outdated recipes** are announced: a Noust release that fixes a vulnerability
  once per release and while the server stays behind, and installed recipes with a newer version when the
  list changes (`noust health` says the same).
- **A reboot that something blocks says what (item 82).** Instead of systemd's one sentence, Noust names
  who holds the shutdown inhibitor and why, and never overrides it.
- **Disk cleanup by itself.** A server cleans its disk above a threshold you set and keeps the history of
  every cleanup.

### Metrics and logs

- **Traffic.** An application's requests by the hour, its top pages, networks, status codes and
  referrers, the time requests took (p50 and p95), different visitors, and an alert on a slow application.
  Latency is recorded only on the access-log format of sites Noust writes.
- **Charts.** An application's disk and network traffic, the swap and load of the server, and every
  container of a stack as its own series. `GET /api/metrics/prometheus` exports the newest value of every
  metric in the Prometheus and OpenMetrics format.
- **Logs.** Search the logs of every application and server with fields, AND, OR and exclusion, open any
  entry in context, set how long the journal keeps them and compare one metric across servers. Logs are
  shipped to Loki, Axiom, a JSON endpoint, OTLP, a remote syslog or New Relic. A site's access and error
  log can be read from the console and with `noust site logs`. Everything the console and the API show
  of a log passes through one redactor that masks private keys, tokens, `password=` assignments and
  credentials in URLs ([logs.md](logs.md)).
- **Probes and a status page.** Ask an address from outside the application, be told when it stops
  answering, and publish a status page on a name ([probes.md](probes.md)).

### Notifications and alerts

- **Channels by name.** Several channels of one type, each with its own events, route and quiet hours;
  failed deliveries are tried again and kept on record. New types: Pushover, ntfy, Gotify, Teams,
  Mattermost, Matrix, Lark and PagerDuty, and email through Resend, Postmark, Brevo or Amazon SES. A
  channel can cap how much it says and send a summary instead of a message at a time. The generic webhook
  sends the headers its receiver asks for. Discord can ping a role or a user when something critical
  fails. A team keeps channels of its own.
- **Sealed secrets.** The webhook URLs, tokens and passwords of channels are encrypted in `config.yaml`
  under a key beside it; the console encrypts what an older file holds when it starts, and
  `noust config protect` does it by hand.
- **Alert rules** can be written, taken by a person (the pager is told somebody did) and reopened. New
  events include `cron_missed`, `cleanup_success`, `cleanup_failed`, `probe_failed`, `probe_recovered`,
  `security_release`, `container_restart_loop` and `recipe_outdated`
  ([notifications.md](notifications.md)).

## Access, teams and automation

- **Teams, projects and environments.** A team groups people and the projects they run, a project has
  environments, and every application, site, database and backup schedule belongs to one project and one
  environment. What a person sees is decided in the data layer: a resource of another team reads as
  absent (a 404, never a 403). An existing server gets one default team, project and environment holding
  everything, and accounts join it with the matching role, so a server nobody organises behaves as before.
  See [teams.md](teams.md).
- **Roles where they are needed.** Give a role on a project or an environment, define roles of your own,
  and bind an API token to a team or a project. A token can be limited to the networks it is accepted from,
  and the API can be turned off for tokens.
- **Single sign-on.** People sign in through an OpenID Connect provider, a SAML 2.0 provider (Entra ID,
  Okta, Keycloak, ADFS), a directory (LDAP, Active Directory) or GitHub, and an identity provider can keep
  the roster of who has an account through SCIM. A group of the provider can give a role on a project or
  an environment. The password and passkey sign-in can be turned off once single sign-on is in, without
  losing the way back, and the choice survives a restart ([sso.md](sso.md)).
- **Email.** An invitation and a password reset can be mailed when the server has a mail channel.
- **Compliance one control at a time (item 83).** A server on the standard profile no longer carries the
  friction of the ENS profile: the administrator holds every permission instead of leaving accounts,
  security settings and the audit log to a security officer and an auditor, a person may hold accounts of
  several roles, and a new account is not held at the second-factor enrolment unless the console is
  published. Each of these, and the others (a reason with every change, a second person for
  root-equivalent actions, a sudo password, restricted tokens...), is a control with its own setting,
  permissive by default, and a framework is a preset over the controls: ENS basic, medium and high,
  ISO/IEC 27001:2022 Annex A, NIS2 article 21, CIS Controls v8 IG1 and IG2, SOC 2 Security and GDPR article
  32. Several can be on and each control takes the strictest value any of them asks for.
  `security.profile: ens-medium` keeps working and means ENS medium is on. A server that relied on the
  stricter roles turns them back on with `noust compliance set roles_split on`, or by enabling a framework
  ([COMPLIANCE.md](COMPLIANCE.md), and what changes on a server you update in
  [UPGRADING-4.0.md](UPGRADING-4.0.md#what-else-changes-on-its-own)).
- **What happens when the audit trail cannot be written.** `audit.on_failure` is `warn` (the default:
  the change goes through and the failure stays loud) or `refuse`, which is always the case under the ENS
  profile: the API answers 503 `audit_unavailable` and the command line exits before doing anything.
- **A terminal.** A terminal in the browser opens as an application's own account, in a container of its
  stack, or as root on the server behind a switch of its own that is off until you turn it on. A command
  can also be run once. Deadline and idle limit are enforced by the runner, and a terminal cannot open
  without a recording kept in the audit ([terminal.md](terminal.md)).
- **Driving Noust from outside.** `noust` can operate another server through its API with a context and
  a token that is never an argument ([remote.md](remote.md)). An endpoint for MCP clients inspects,
  deploys and controls through the API with the guardrails written once ([mcp.md](mcp.md)). The API
  reference in the console tries any call with the session. A typed Python client is generated from the
  API's schema ([sdk.md](sdk.md)), and a Terraform and OpenTofu provider manages applications, domains,
  databases, backup schedules, teams, projects, environments, members and shared variables, proved against
  the real API and ready for the registry ([terraform.md](terraform.md)).
- **Tags and ownership.** Fleet servers can be given to a team, which sees them and works on them through
  the central, and databases and services carry tags.

## Fleet

- **Create servers at a provider.** A central can create servers at Hetzner, DigitalOcean or Vultr,
  enroll them, power them on and delete them. The host key is chosen by the central and pinned before the
  machine exists, the login that installs Noust is a key of one use, and no fleet token travels in
  user-data. A library of cloud-init scripts is validated on save ([CENTRAL.md](CENTRAL.md)).
- **Servers behind NAT.** A server with no SSH port the central can reach runs `noust fleet link` with
  the command the central's invitation printed, and keeps one connection open to the central. A server can
  also be reached through a bastion, and the wait for a connection and other SSH options are set per
  server.
- **Updating to a version (item 81).** The plan of a Noust update names, per server, the version it would
  run, also for a server whose package is still being published; the update takes a target version, and a
  server that stops short of it fails and says how to go on. "Refresh package lists" is an action.
- **Watching servers.** A server is declared down after a threshold, asked less often the longer it is,
  and can be paused. Metrics of every server can be compared and logs searched across them.
- **An application on several servers.** `noust fleet placement` records where an application runs, shows
  which commit each server serves, rolls it out as one commit (the first server goes alone, and the others
  are asked for the commit it ends on), rolls an image tag out, copies the application to a server that
  lacks it and writes the list of servers for a load balancer. A server that stops short is put back by
  its previous release. The central still routes no traffic and deploys nothing itself.
- **One server builds, the others run.** `noust fleet builder set shop.example.com build-1` makes a
  server build an application once; `noust fleet placement ship` carries the release to the others in
  resumable chunks, checked by digest, and each activates it behind its own health gate. An update
  everywhere follows the build server unless `--build-on-each` says otherwise.
- **Moving things.** A database moves to another server of the fleet, a server moves to another central
  with what the first knew, a server can be linked to its machine at a provider, and a node's key can be
  rotated by the API and the console.
- **Mixed fleets.** A central of 4.0 with a node of 3.3.1, and the other way round, were run together for
  deploys, the event stream and exports.

## Recipes and catalogue

- **143 recipes, up from 6.** 128 can be installed (5 native, 123 in Docker) and 15 are listed with the
  reason they cannot. Every image is pinned by tag and by digest, the lint that runs in CI refuses a tag
  that moves, and every recipe starts closed. 105 were written from the project's own documentation and 23
  took part of their wiring from Coolify's service templates (Apache-2.0), credited in the header of their
  file ([recipes.md](recipes.md)).
- **Proved in Docker.** 97 of the 128 recipes you can install were started for real and answered their
  health check: the sixteen Compose recipes of the first wave were each installed, served through nginx,
  restarted and deleted; WordPress, Uptime Kuma, n8n and Umami are installed and deleted by the integration
  suite; and 77 more were started by `scripts/recipe_smoke.py` against the image's own configuration (each
  one ran its stack, passed its health check through nginx's headers and ended with no restarts and no
  errors in its log). That run changed the recipe itself in 15 cases (a health path, a port, a password
  rule, a mount, a driver the image lacks). The other 31 are linted, loaded and pinned, but have not been
  started: `scripts/recipe_smoke.py NAME` is the way to try one.
- **Using the catalogue.** Search by name, category, tag and kind; install from the console, the CLI or the
  API; `noust recipe outdated` shows which installed recipes have a newer version, and an update goes
  through the one update with its backup, health gate and way back. What a recipe needs from the server is
  checked before anything is created, and a recipe starts with daily backups of its databases and data
  unless `--no-backups` says otherwise. A first deploy that fails says which databases it made were kept.
- **Recipes with several public addresses.** A Compose recipe can declare up to four extra addresses; an
  update that brings a new one asks for it and adds it after the health gate.
- **Your own recipes.** An operator can keep recipes of their own, a recipe can ship configuration files
  filled with its variables, and the catalogue can grow between releases from a signed package, never from
  a poll. [contributing-recipes.md](contributing-recipes.md) is the guide for adding one.
- **Templates from other platforms.** Coolify templates convert to a Compose document and a `.env`
  (tried over 382 templates), and Dokploy templates convert to a Compose stack with the files they mount
  (541 of 572 converted, the rest refused with their reasons).

## Installation and importers

- **One command installs Noust.** `install.sh` reads the platform, refuses a release the table does not
  declare, adds the repository only after checking the key's fingerprint, installs with `apt`, `dnf` or
  `zypper` (the RHEL family through pip), runs setup and prints how to reach the console. A second run
  leaves existing configuration alone. It downloads no other script and is published with signed
  checksums ([PLATFORMS.md](PLATFORMS.md)).
- **Unattended.** `--admin-user` with `--admin-password-file` (or `NOUST_ADMIN_PASSWORD`), `--admin-email`,
  `--auto-update` with its hours and days, and `--json` for one document at the end. A cloud-init guide has
  the user-data file and an Ansible play ([cloud-init.md](cloud-init.md)).
- **Platforms.** Debian 12 and 13, Ubuntu 22.04, 24.04 and 26.04, Fedora, openSUSE Leap and Tumbleweed,
  and the RHEL family (Rocky Linux, AlmaLinux, Red Hat, CentOS Stream and Oracle Linux) through pip, on
  amd64 and arm64. Arch and Alpine are refused with their own reasons. [PLATFORMS.md](PLATFORMS.md) says
  what was tested where.
- **Minimums.** Memory, processors and free disk have a floor (below it Noust is not installed: 512 MB, one
  processor, 3 GB), a minimum (below it the installer asks and `noust setup doctor` warns: 1 GB, one
  processor, 10 GB) and a recommended size, declared once. `noust setup doctor` also says who holds ports
  80 and 443 and how to free them ([requirements.md](requirements.md)).
- **After install.** An installation check and a report for an issue are in the console and the CLI, Noust
  checks before it updates itself and keeps a way back, and a server can install Noust's own releases by
  itself in the hours, on the days and for the releases you chose.
- **Bring applications over.** The importers read Coolify, Dokploy, CapRover and Dokku and bring each
  application's storage and the databases it connects to, with the commands that copy their data; a value
  that looks like a credential is withheld from the proposal, and a name another platform supplies is never
  put in a command you paste unchecked. Guides: [coolify.md](coolify.md), [dokploy.md](dokploy.md),
  [caprover.md](caprover.md), [dokku.md](dokku.md). Taking over a server Coolify runs was run against
  Coolify 4.4.6. [leaving.md](leaving.md) says what keeps running if Noust is removed.
- **A remote client** on a laptop or in CI installs without the server's dependencies
  ([remote.md](remote.md)).
- **Fixed on the way.** `noust setup init` names the distribution on Debian 13 instead of "Unknown", and
  installing a package on a server whose apt lists are empty (a fresh image, a container) updates them
  once instead of failing with "Unable to locate package".
- **Examples.** `examples/` holds a minimal application for every type Noust deploys, and the framework
  guide points at them ([frameworks.md](frameworks.md)).
- **Documentation.** A static, searchable, versioned documentation site is generated from `docs/`, every
  console page links to the guide that explains it, and the release notes are readable in the product.

## The console

Everything here is compared with the console of 3.3.1. Screens that did not exist then are described
where they are, not as a change.

### One register for every screen

- **Every screen is written once.** The sidebar, the tab strips, the settings list, the breadcrumbs, the
  `g` shortcuts and the command palette read one register, and a test holds it to the router in both
  directions. An old address redirects to its new one (see [Where things moved in the
  console](#where-things-moved-in-the-console)), and an old name stays a search word in the palette and
  in the filter of Settings.
- **Texts load by area.** The first screen carries the words it draws and nothing else; the rest of the
  language arrives while the browser is idle, so a console with many more screens does not make the first
  one wait for them.
- **A third language is a JSON file** anyone can contribute without TypeScript, held to English by tests
  ([TRANSLATING.md](TRANSLATING.md)).

### Finding your way

- **A scope selector.** At the head of the sidebar (in the top bar on a narrow screen) it says whether the
  console is on this server, on one of the others, on **All servers** or on **This central** (3.3.1 had
  this selector in the top bar). It is not drawn on a lone server with nobody else added.
- **The sidebar follows the scope.** On a server: Overview, Projects (where teams, projects and
  environments live), Applications, Databases, Domains, Backups and Cron; then Logs and Activity; then
  Server; and Settings pinned under them. On **All servers** or **This central**: the fleet's seven pages
  (Overview, Servers, Applications, Certificates, Backups, Updates and Activity). The strip of tabs the
  fleet's pages drew is gone, because the sidebar lists the same pages; only Applications keeps a strip,
  with its second view, **On several servers**.
- **Recipes, the map and teams are views, not areas.** The catalogue is no longer an area of the sidebar:
  Recipes and Installed recipes are under Applications (they keep the address `/catalog`). The map of the
  server is the second view of Overview, and Teams is a tab of Projects.
- **The inbox.** A bell in the top bar opens **Needs you**, a state and not a story (a failed application,
  a certificate about to expire, an alert that fires, a probe that is down, a request waiting for your
  decision, a new Noust or a recipe with a newer version stay there while they are true, whether or not the
  console was open when they happened), and **Recent**, what the server said while this tab was open (30 at
  most; the history is Activity). A row has its severity, what is wrong, the system's own words in mono and
  one button that goes to the thing. It is a popover from the `sm` breakpoint up and a drawer on a phone.
  Alerts are taken and reopened from it.
- **The account.** The avatar menu opens **Account** (profile and password) and **Sign-in security**
  (authenticator app, passkeys and where you are signed in). Settings > Security is gone:
  `/settings/security` opens Sign-in security, and what concerned the server (the security profile, the
  compliance frameworks and the controls) is Settings > Controls.
- **Settings in six groups that say whose they are.** This server, Monitoring and Developers describe the
  server you chose; People and access, Governance and Fleet are the instance's. A filter appears from twelve
  sections and finds a section by the name it had before, and on a phone the list is a page of its own.
- **The palette** lists every screen of the register with where it lives, takes `>` for actions and `@` to
  change scope, remembers the last three places, finds applications, databases, sites, certificates,
  services, servers and recent deployments, and runs verbs such as restart, stop, start, roll back and
  update. Every area of the sidebar has a `g` shortcut.

### The way in

- **The installer ends with what to do next.** It prints the command that opens the tunnel to the console
  with this server's address in it (a placeholder, and a sentence saying so, when it cannot tell), and the
  link that opens the sign-in on the access token. The token is shown once, in the box above.
- **The access token is the first administrator's way in.** The sign-in button says **Access token** (it
  said "Emergency access"), the page opens on the field, and one form creates the first administrator and
  signs in with it. **Protect your account** can wait (**Set up later**), except under a framework that
  asks for a second factor or on a console that is published or listens beyond loopback. The token that
  signed in is held in the page's memory for one use and handed to **Confirm it's you** unasked, so creating
  the first account does not stop on a dialog; it is never stored.
- **An empty server offers a first deploy.** The Overview shows one card, **Deploy your first app**, with
  **Try a demo** (a tiny page on a temporary address, one click), **Use a repository**, four popular recipes
  and **All recipes**, and under it **Then make it safe to leave alone** (backups, a copy off the server,
  notifications and, while the account is not protected, **Protect your account**).
- **The wizard reads what you give it.** A repository or a path pasted in the source step is read at once
  and a line under the field says what was found, with **Change** (`Detected: Next.js with npm. Port 3000.
  2 variables to fill in.`). The ways to start are **From GitHub**, **URL or path**, **A Docker image** and
  **More ways** (Compose, a recipe, an import, a site already running). A server with no domain of its own
  uses a temporary address (`shop.203-0-113-10.sslip.io`, made of the server's public address, so it resolves
  here) and the first deploy needs no DNS; the summary says what was detected, the address and the variables,
  each with **Change**.
- **GitHub from the wizard.** On a server with no GitHub App the source step is the form that connects it,
  and the wizard is found where it was left when GitHub sends the browser back. A private repository
  reached over SSH that cannot be cloned shows git's own words, this server's public key in a box to copy and
  the page of the repository where it is added.
- **The deploy log opens with what was detected**: `Detected Node.js (package.json). Install: npm ci. Start:
  npm run start. Port 3100.`, before anything runs.
- **A recipe or an import ends on one card** that says it is live, the log, the address with copy and open,
  whether HTTPS is ready (`HTTPS is ready.`, or `Served over HTTP: the certificate could not be issued.` with
  **What certbot said** verbatim), and the next steps: your own domain, a database, a backup.
- **The catalogue is a page of tiles**: the logo, the name, one line and **Use**, with a **Popular** row
  before any filter.

### The application's page

- **What is live and what is not applied.** **Update** is a split button (its menu has
  **Update without cache**, and on releases **Build without serving**), and Restart, Stop or Start and Roll
  back are beside it. "N not applied" and "Deploys paused" are chips on the state line, not a banner. The
  overview shows **Live now**, the last deployments (and which version kept serving through a failure), one
  health section with uptime, certificate, CPU and memory, previews, where the application answers and
  whether a push deploys it. With the service down the page says **Last version that served** and no row
  says Live.
- **One list of deployments.** Search, state, branch and who started it filter the list, and the filters are
  in the address; a press on a row opens it, its menu rolls back, and the build that waits is at the top
  with **Go live**. The filters look through the whole history the server keeps, not only the rows on screen.
- **A failed deployment** shows the error and **Retry** (or **Retry without cache**) on the first screen at
  1440 and at 390 pixels wide, says which version still serves, opens the log on its first error, shows the
  tool's words without its colour codes, and has **Open Diagnose**, **Edit environment** and **Show it in
  the log** beside it.
- **Variables.** A variable is changed where it is read, a hidden value is never read to edit it (**Replace
  value** or **Keep current value**), the row shows the old value struck and the new one beside it, and the
  bar saves only what changed (**Save and restart** or **Save only**).
- **Domains.** A domain is added in one press with its DNS asked when the field is left, and removing one
  is one question.

### The application's settings

- **Six groups.** Code (Source, Build, Container), Deploys (Releases and rollback, Automation, Compose),
  Triggers (Deploy on push, Deploy hooks, Previews), Runtime (Resources, Processes, Mounts, Disk, Service
  security), Access (Access, Protection) and Export and delete. In 3.3.1 there were nine sections in one list.
  Build commands and Builds are one page, **Build**, with the build sandbox as a card (`/settings/builds`
  opens it). Automation holds automatic deploys, the pause after a rollback, the backup an update requires,
  the copy of a stack's databases, freezes, scheduled updates and deploy limits; Compose holds a stack's
  options, networks and registry accounts; Protection holds limits, headers, HTTP/3, the firewall and client
  certificates.
- **One way of saving.** These pages save from one save bar or a drawer, or their rows apply at once; there
  are no per-card Save buttons and no banners. Deploy on push is a stepper that draws the step the setup is
  at, and does not send anyone to a terminal when this server's GitHub App covers the repository.
- **Shorter pages.** The length of every settings page is measured at 1440 by 900 and may only go down.

### Settings, the server and access

- **Settings rows.** Console address, MCP, Approvals and Automatic updates say their state as a pill and
  are rows, not blocks. Single sign-on is one list of connections (OpenID Connect, SAML, directory) with one
  menu to add and one per row, and its empty states are one line.
- **One confirmation dialog.** Every question of the Server area (reboot, SSH fix, firewall, bans, storage,
  swap, services restart, cleanup) is the same dialog, with the server's name typed for the dangerous ones
  and the system's own refusal shown verbatim. A pending SSH or firewall change is a sentence with its
  countdown and a drawer, and the reboot notice is one line with a **Why** button.
- **About Noust** is three sections and shows the copyright, the licence with its text one click away, that
  there is no warranty and where the source code is (item 85).
- **The terminal** opens from one line, and the ENS report and the audit log say their verdict as a pill.
- **Renamed sections:** see the table below.

### Logs, alerts, backups and servers

- **One page for logs.** **Logs** searches the applications or the system's journal (the **Where from**
  choice; the system's journal is no longer Server > Logs, and `/server/logs` opens it). The same toolbar is
  on every place that reads logs, an application's Logs tab included: a query box, the range (**Live** is the
  first range and there is no Live switch), the syntax help and **Copy link to this search**. An
  application's range can be **Since the last deploy**, and the line `Deploy #N started here` marks it.
- **Alerts are taken and reopened with who and when**, in the settings row and in the inbox. The built-in
  checks are boxes, and their number appears only while they are on; an empty field no longer means off.
  The inbox, the sidebar and the settings pages share one answer to "which alert needs somebody", and a
  probe that is switched off no longer counts as down.
- **Channels, probes and the status page are rows.** The master switch of Channels is a row with its state,
  events are groups ("4 of 5 sent"), and the metrics export is part of **Logs and metrics export**.
- **Databases.** The list counts the databases with no backup schedule in a filter (**No schedule (N)**)
  instead of a banner, restoring is one confirmation, and **Allow changes** in the query editor is a box with
  its risk on the same line.
- **A backup schedule is built from the same fields as every other one**: when, how many to keep, where to
  send, and what not to keep here. Each destination is a block that fits a phone, and a retention limit is a
  box that is ticked with the number it reveals, so "no limit" is a state the page shows and not an empty
  field. "Schedule" is the only word for it.
- **Servers are one list.** **Fleet > Servers** holds what Settings > Servers held (pause and resume
  monitoring, the quiet reminders, creating a server at a provider); `/settings/servers` opens it with its
  filters and the **Add a server** flow, and **Add a server** in the selector and the palette goes to it.
  Removing a server is one question.
- **Domains** list their DNS records, tunnel routes and site versions in the kit's list with state pills, a
  site's editor asks once before it is left, and the DNS verdict of adding a domain is one line with its
  records one press away.

### Forms, loading and leaving a page

- **A form does not switch its button off for a missing value.** Pressing says what is missing and focuses
  the field (in the forms of tokens, roles, accounts, services, the API reference, the manifest and the
  schedules of Backups).
- **One question before leaving unsaved changes.** A form with unsaved changes asks once, with **Keep
  editing** focused and **Discard changes** beside it.
- **Nothing moves as it loads.** Lists, cards and pages load through one skeleton, error and empty state,
  and a test opens every route against a server that never answers.

### Words and icons (item 86)

- **One word for each thing, in both languages.** The Spanish console said Borrar, Eliminar and Quitar for
  the same act and Leyendo for loading; it says Eliminar for delete, Quitar for remove and Cargando for
  loading, one name for each state (En ejecución, Fallido), and archivo for a file. In English each thing has
  one verb (`Retry` where it said "Deploy again", `Continue with GitHub` and `Continue with GitLab` where it
  said "Confirm and continue", `Open access` and `Close access` for a database made reachable from the
  network). A glossary of the interface and a test hold the words ([TRANSLATING.md](TRANSLATING.md)).
- **Object icons.** Every card, section and group of settings across the console, the verb buttons and menu
  items, and the menus of single sign-on carry the icon of the object or the verb they are about:
  achromatic, decorative and never in a tab. One map holds the icon of each kind of object (an application,
  a database, a domain, a backup, a server...), the register and every screen read it, and a lint rule
  refuses an object's icon taken from anywhere else, so an object has one icon across the console.
- **On a phone.** The action of a notice and the button of a setting go under their text at 390 pixels, where
  a Spanish sentence was left about 100 pixels wide beside them.
- **Four more languages.** Besides English and Spanish, the console speaks French, German, Brazilian
  Portuguese and Simplified Chinese, every text of it. A browser that asks for one of them gets it (Portuguese
  and Chinese in any form but Traditional Chinese), and the session menu switches. Each language is a file
  only who chose it downloads; a text a later release adds shows in English until it is translated
  ([TRANSLATING.md](TRANSLATING.md)).

### Other changes across the console

- **The kit and its rules.** Setting rows with their risk, lock and confirmation, one confirmation dialog,
  split buttons, row lists and row menus, skeletons with the shape of what they stand for and dashboard and
  settings templates are in the kit, with a gallery of all of it in both themes. Twenty-seven structural
  rules, the glossary of the interface and the length of settings pages are held by tests, and
  [DESIGN.md](DESIGN.md) says how a setting is a row, when a notice is allowed and who saves and confirms.
- **Daily work.** Lists page at 20, 50, 100 or all rows, remembered per list.
- **New application.** From the Applications list, a new application from a public repository is four
  clicks and from a recipe three, and a container application is made from a Dockerfile, Railpack or a registry image in the wizard (an
  image from a registry is no longer proposed 8080, the console's own port, but starts from 3000).
- **A map** shows how the server fits together, read from it, with nothing to arrange.

## Security and licences

- **What comes from other projects ships with its credit and its licence.** `NOTICE` lists the recipes
  derived in part from Coolify's Compose templates (Apache-2.0), the ClickHouse files of the Plausible
  recipe (MIT), the console's fonts (OFL-1.1) and the Dokploy template excerpts used in the tests (MIT).
  The licence texts are beside `LICENSE` (`LICENSE-APACHE-2.0`, `LICENSE-OFL-1.1`), in the Debian copyright
  file and in the RPM's licence files, and a test keeps the list of derived recipes in step with their
  headers. About Noust in the console carries the AGPL-3.0-or-later notices (item 85).
- **Contributing.** One guide for contributors, `CONTRIBUTING.md`, which also says under which licence a
  contribution is made. The 2.x guide that GitHub showed first is gone.
- **Signed releases.** From 4.0.0 on, a release tag is signed with the maintainer's SSH key, which GitHub
  shows as verified; earlier tags are not signed. Every file a release carries has a keyless-signed `SHA256SUMS` and a build-provenance
  attestation, the wheel has its SBOM attested, the container image is signed and attested by digest,
  PyPI publishes with attestations, and a fixable critical vulnerability in the image holds the release
  back. The installer is published with its own signed checksum. The wheel and the source distribution
  can be rebuilt from the tag byte for byte ([reproducible-builds.md](reproducible-builds.md)).
- **A release is announced when every distribution serves it.** The GitHub release is created as a
  pre-release, and only once the package repositories serve the version does it become the latest, so no
  update check points at a package that is not there yet.
- **Security releases are told apart.** A release that fixes a vulnerability is titled "(security
  release)", and the update check, `noust health` and the console say so. `SECURITY.md` lists the
  supported versions and the release check fails when it does not list the release being made.
- **Reporting a vulnerability.** The private advisory form on GitHub is the preferred channel and is
  turned on; `.well-known/security.txt` (RFC 9116) is renewed with every release. `SECURITY.md` commits to
  acknowledging a report in 3 working days, confirming it in 10 and fixing it in 90.
- **Checks on the code.** CodeQL (Python and TypeScript), gitleaks over the whole history, Trivy on the
  container image and the lock files, and a dependency review that refuses a pull request adding a high
  or critical advisory.
- **Hardening across the product.** Backup destinations and source archives are judged by the outbound
  guard; IPv6 clients are limited by their /64; every response carries the same security headers and no
  `GET` changes anything (a test holds it); a credential holds at most sixteen event streams, each for at
  most twelve hours; every allowlist matches the whole text and every class of input has an adversarial
  suite; secrets stay out of a store dump and an incident's evidence; a Compose stack is kept to its own
  files and a monorepo runs as an account of its own. [SECURITY-MODEL.md](SECURITY-MODEL.md) says what
  each guarantee covers and which test holds it.
- **Posture and privacy.** `noust health` and the API have a security-posture check: backups that leave
  unencrypted, a central's keys that are not sealed, a security release that is out. [PRIVACY.md](PRIVACY.md)
  lists every request Noust makes, including the email services, the probes and the directory sign-in.

## Where things moved in the console

An address that changes redirects to the new one, and a screen that changed its name is still found by
its old name in the command palette and in the filter of Settings. Every screen of 3.3.1 that moved or was
renamed, with where it is now and what its old address does, is the table in
[UPGRADING-4.0.md](UPGRADING-4.0.md#where-it-is-now).

## Removed

- **The `wasm` command**, with its entry point, its completions and its manual page. Use `noust` with
  the same arguments.
- **`noust migrate-from-wasm`**, the notice that told you about the rename, `NOUST_NO_AUTO_MIGRATE`, the
  `WASM_*` spelling of every `NOUST_*` variable (`WASM_APPS_DIR`, `WASM_WEB_STATE_DIR`...), and the
  resolution of directories and of a store under the old names. 3.3.x does the move; 4.0 does not.
- **The transitional packages** `wasm` (Debian) and `wasm-cli` (RPM and PyPI) are no longer built or
  published. `noust` is the only package, and it declares a conflict with the 2.x packages so `apt` and
  `zypper` do not mix them. Anyone who still has a transitional package can remove it.
- **The links 3.x left**: `/etc/wasm`, `/var/lib/wasm`, `/var/backups/wasm`,
  `/etc/nginx/wasm-upstreams`, `~/.local/share/wasm` and `/usr/local/bin/wasm`. The package retires them
  when nothing uses them, and `noust setup retire-links` does the same on demand; a link that something
  names stays and the command says what names it.
- **The `renamed_from_wasm` field** of `GET /api/auth/session`, and the console's one-time notice about
  the rename.
- **The `linked_app` key** of `noust db list --json`, which repeated `app` under the name 2.x used.
- **In the console**: Settings > Security (see the table), Settings > Servers (Fleet > Servers is the one
  list of servers), the Logs tab of Server (Logs > System), the strip of tabs of the fleet's pages, and, on
  the application's page, the "How it runs" section, the deploy dots, the Releases section of Deployments
  and the "Environment saved" step.

## API

The API gains a version and 617 operations, and removes none. The complete list, release by release and
operation by operation, is [API-CHANGELOG.md](API-CHANGELOG.md). It lists this release as one section, 4.0.0,
and puts what can break a client first.

- **Breaking.** None removes an operation, a parameter or a field. What a client of 3.3.1 can notice:
  the session cookies and the CSRF header carry Noust's name (`noust_session`, `noust_csrf`, `X-Noust-CSRF`;
  the header of before 4.0 is still accepted); `GET /api/auth/session` no longer answers `renamed_from_wasm`,
  which a client reads as absent, which is what `false` meant; an application's export says `noust-app` and
  `noust_version` (both spellings are read); a backup destination that does not connect is refused with
  `400 destination_unreachable` unless `verify` is `false`; the administrator holds the security officer's
  and the auditor's permissions on the standard profile; and an update is refused when the backup it needs
  failed.
- **Versioned.** Every route is also served at `/api/v1/...`, a response under `/api` carries
  `X-Noust-API-Version: 1`, and a deprecated operation announces itself with `Deprecation`, `Sunset` and
  `Link` headers. None is deprecated. The compatibility promise is held by a test against the API as 3.3.1
  shipped it, and all 433 operations of that baseline are kept.
- **Added.** The API describes 1,050 operations, counted in `panel/openapi.json`, up from 433. The new
  areas include teams, projects and environments, database instances, replicas, imports and recovery to an
  instant, the runtime of an application (processes, copies, hardening, tuning, mounts, files, disk, pending
  changes), the deploy queue, recipes, uploads and pushes, volume and Noust-state backups and their
  encryption, backup failures, the restore of a whole server, fleet placements, build servers and cloud
  servers, release bundles, domains and per-name certificates, DNS accounts, the private authority, per-site
  policy, notification channels and alert rules, probes, traffic, log search and shipping, single sign-on
  (OpenID Connect, SAML, LDAP, SCIM), compliance, the terminal, the console's exposure on a name, the
  manifest and the MCP endpoint. Existing operations gained fields: for example `audience` on
  `PATCH /api/apps/{domain}/env`, `no_cache` on a rebuild and `remove_volumes` and `remove_databases` on
  deleting an application.
- **Changed, without breaking a client.** `GET /events` answers `429` to a credential that already holds
  sixteen event streams and ends a stream after twelve hours (the browser reconnects by itself); the
  anonymous rate limit, the sign-in lockout and the webhook budget count an IPv6 client by its /64; the
  text of the logs endpoints has credentials of a recognisable shape replaced by `***`.
- **Removed.** Nothing.

<!-- release summary: Teams, container apps, 143 recipes, engines in containers, per-name certificates and a rebuilt console; the wasm command, its packages and the old names are retired. -->
