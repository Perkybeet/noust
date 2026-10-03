# Noust 3.3 changelog

Changes since Noust 3.2.1. Upgrade notes are in [UPGRADING-3.3.md](UPGRADING-3.3.md); the
databases area is described in [databases.md](databases.md).

3.3 shows the databases a server really has. Until 3.2 the Databases area only saw engines
installed on the host, so a database in a Docker container (most of them, on the servers this
release was measured against) did not exist for Noust; an engine that refused Noust's account was
shown as an engine with nothing in it; and an application was linked to a database only if Noust
had written its connection string. None of that changes anything on the server by itself: what is
new is what Noust sees and says, and the actions it offers.

## Databases in containers (item 67)

- **Instances.** A database server is now either the host's engine (as before) or a container
  Noust finds on its own: the official images of PostgreSQL, MySQL, MariaDB, Redis, Valkey and
  MongoDB and their common derivatives (PostGIS, TimescaleDB, pgvector, Bitnami). Each one is
  named by a key that stays the same when the container is recreated:
  `postgresql@<project>.<service>` for a Compose service, `postgresql@<container>` otherwise.
- **The same code as the host.** Listing databases, users and tables, sizes, the data browser,
  read-only queries, dumps, restores, backup policies and links all work on an instance: the
  engine's own client runs inside the container through `docker exec`. The container's
  passwords are read inside the container and never reach the host's process list; the ones
  Noust holds itself travel in the environment of the `docker` process.
- **Whose it is.** A container's databases belong to the application its Compose project is
  (the project label against the application's project or name), so the Proggest stack's
  Postgres and Redis show as proggest.es's.
- **What differs.** Start, stop and restart act on the container. Installing, uninstalling and
  configuring do not exist for an instance: the image decides the engine, and Noust says so.
  When only the application's own account is available (no root password in the container's
  environment), the instance is marked as limited access.
- Every `--engine` option of `noust db` takes an instance key, and `noust db engines` lists the
  containers after the host's engines.

## An engine that does not let Noust in says so (item 68)

- A listing that failed used to come back empty: a MySQL that asked root for a password showed as
  a server with no databases and no users, and its tracked databases as gone. Now the engine's
  own answer is shown, verbatim, with the fix above it, and the tracked databases of an engine
  that could not be read are marked "not verified" instead of missing.
- For MySQL/MariaDB and Redis the fix is in the console: store the account Noust signs in with
  (`PUT /api/databases/engines/{engine}/credentials`, sudo mode, audited as
  `db.credentials.set`). The engine is asked first, and nothing is saved if it refuses.
  `noust db config` goes through the same check, and `--password` alone asks for the password
  without echo, so it never reaches the shell history or the process list.
- PostgreSQL and MongoDB do not sign in with a stored password; their message says what Noust
  needs instead (peer authentication for `postgres`, the administrator Noust created).

## Links that already exist (item 69)

- Noust reads each application's environment and finds the databases it names: connection URLs
  (`DATABASE_URL`, `REDIS_URL`, Prisma, Laravel, JDBC, driver suffixes) and the `DB_*` and
  `REDIS_*` sets. A reference resolves to one engine: by port on this machine (a container's
  published port included), by service name inside the application's Compose project, never by
  guessing between two. Nothing of the application runs, no password is kept or shown, and a
  `.env` that leads outside the application's tree is not read.
- The databases list shows those applications as **detected** beside the database. "Record the
  link" makes one a link without touching the application (`POST .../links/detected`, audited as
  `db.link.record`), one at a time and only when the operator asks: an application's `.env` is
  written by whoever deploys it, so what it names is a claim. A database another application owns
  is refused, an existing link is never overwritten, and the recorded link carries no account, so
  a later password rotation never writes the new password into an application that merely named
  the account. `noust db adopt` tracks databases and records no links.

## Counts (item 70)

MySQL counts tables in its listing; Redis reports keys rather than tables; a count the listing
does not know is shown as unknown, never as 0.

## Engines: versions and settings (item 71)

- **Choose what to install.** PostgreSQL 14 to 18 (from the PostgreSQL project's repository, or
  the distribution's when it is the one asked for), MariaDB 10.11, 11.4 or 11.8 (from MariaDB's
  repository) or the distribution's, MySQL from the distribution, Redis or Valkey, MongoDB 7.0
  or 8.0, as far as each upstream publishes for the server's release. Every upstream signing key
  is pinned by fingerprint and checked before apt trusts it, MongoDB's included. MySQL and MariaDB,
  and Redis and Valkey, cannot be installed beside each other. `GET /api/databases/engines/catalog`
  and `noust db catalog` say what this server can install; `noust db install postgresql --version 17`.
- **Settings.** A closed set per engine (connections, memory, listen addresses, slow-query
  logging, time zone...) with the current value, a recommendation for this server's memory and
  cores, and whether it needs a restart: `noust db settings <engine> [KEY=VALUE ...]`, the
  console's Settings page, `GET/PUT /api/databases/engines/{engine}/settings` (sudo mode, audited
  as `db.settings.change`). Values go into Noust's own include file, never the distribution's;
  each change is checked with the engine's own tool, applied (reload, restart or at runtime, as
  the setting needs), and put back with the engine's journal in the error if the engine does not
  come back. Opening an engine beyond the loopback asks for confirmation, and the `ens-medium`
  profile refuses it.

## Exposure without false alarms (item 72)

- A port Docker publishes that the `DOCKER-USER` chain refuses on the public interface is shown
  as closed by the firewall, not exposed; an IPv6 publication on a server without an IPv6 route
  likewise. The database exposure check reads the chain with the same code as the server's
  security check.
- An image that only mentions a database in its name (`zabbix/zabbix-server-mysql`) is no longer
  taken for one: there is one reader of images, which compares the repository name.

## Compose stacks running outside their unit (item 73)

A Compose application whose unit is stopped while its containers run (started by hand with
`docker compose up -d`) used to show as stopped and raise "the unit is not running" while it
served. It is now its own state, "Running outside Noust" (amber), in `noust list`, the console,
the overview and the fleet summary; the monitor says once (`app.outside_unit`) that a reboot would
not bring it back. `noust app reclaim <domain>` (and the button on the application's page,
`POST /api/apps/{domain}/reclaim`, sudo mode, audited as `apps.reclaim`) enables and starts the
unit after the same `docker compose up --dry-run` rehearsal adoption uses; if that rehearsal would
recreate a container it refuses unless `--accept-recreate` is given.

## Fixes

- A Redis container counts as installed even when the host has no `redis-cli`.
