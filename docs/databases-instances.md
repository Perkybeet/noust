# Instances: another version, or an engine the host lacks

Part of [Databases](databases.md).

The engine installed on the host stays the server's primary one. When you need PostgreSQL 16 beside
17, MariaDB beside MySQL, or a Valkey the host does not have, Noust runs the engine itself, as a
container it owns:

```bash
noust db instance catalog                         # what it can run, and the versions
noust db instance create pg16 --engine postgresql --version 16 --memory 512m
noust db instance list                            # its instances, and how much memory they hold
noust db instance show pg16
noust db instance stop pg16
noust db instance start pg16
noust db instance upgrade pg16                    # the newest image of version 16
noust db instance remove pg16                     # asks first: its data goes with it
```

What an instance is, exactly:

- **The engine's official image, pinned by tag and by digest.** `postgres:16@sha256:...`, never
  `latest` and never a tag alone: an instance made today and one made in a year are the same
  software until an upgrade is asked for. The digests are the ones the registries served when the
  catalog was written, read by `scripts/database_pins.py check`, not typed. An image is pulled once;
  a restart never has to fetch it.
- **Published on 127.0.0.1 only**, on a port above the engine's own (5433, 3307, 6380...) or the one
  you give with `--port`. Docker publishes on every interface unless told otherwise and goes around
  the host's firewall when it does, so Noust never asks it to. To reach an instance from your own
  computer, use the SSH tunnel `noust db connect-info` prints, or make one of its databases public
  (`noust db public`, [Making a database public](databases-settings.md#making-a-database-public-on-purpose)),
  which forwards a port you choose to the instance's loopback port.
- **A memory limit, always** (`--memory 512m`, `2g`). The container is held to it by the kernel, and
  the engine is told to size itself to it (PostgreSQL's `shared_buffers`, InnoDB's buffer pool,
  Redis's and Valkey's `maxmemory`, MongoDB's WiredTiger cache), so it is not killed for growing
  past its limit. Each engine has a least it starts in (PostgreSQL 256 MB, MySQL and MongoDB 512 MB,
  Redis and Valkey 64 MB). The limits of all instances may add up to 90 % of the server's memory;
  `noust db instance list` says how much is left. The host's own engines and the applications are
  not counted.
- **Its data in a Docker volume Noust names** (`noust-db-pg16-data`), and its Compose file, its
  environment file and its unit in `/var/lib/noust/db-instances/pg16/` (root only) and
  `noust-db-pg16.service`, so it comes back after a reboot. Noust writes the Compose file: do not edit
  it. `noust db instance upgrade` and `remove` are how it changes.
- **A superuser whose password Noust generated** and keeps in the instance's environment file, which
  only root reads and the container is started from. It is in no command line, no log and no API
  answer. Redis and Valkey cannot read it from the environment, so it is also in their configuration
  file in that directory.

Once it exists it is an engine like the others. It is `postgresql@pg16` wherever `--engine` is taken,
it is listed by `noust db engines` and the console's Databases area, and the same code that drives
the host's engine drives it through `docker exec`: the data explorer and the SQL console, accounts
and profiles, dumps and their policies (one database, or every database of the instance), restores,
imports from another provider, and making a database public.
Dumps of an instance are named with its key (`postgresql.pg16.shop-...`) and never mix with the
host's. Everything below that says "an engine" says it of an instance too.

| Engine | Versions Noust offers (default first) |
|---|---|
| PostgreSQL | 18, 17, 16, 15 |
| MySQL | 8.4, 9.7 |
| MariaDB | 11.8, 11.4, 10.11 |
| Redis | 8.0, 8.4, 7.4, 7.2 |
| Valkey | 8, 9, 7.2 |
| KeyDB | 6.3 |
| ClickHouse | 26.8, 26.3, 25.8 |
| MongoDB | 8.0, 7.0 |

`--version` matches by components (`8` is MySQL 8.4; `11` is ambiguous for MariaDB and says so).
A new version is added to the catalog by a release of Noust, after reading it from its registry.

**Versions whose project no longer supports them.** An application that still needs MySQL 5.7 or PostgreSQL 13
can have it beside the current one. These versions are in the catalog too, pinned by digest like the rest, each
with the date its project stopped fixing security problems:

| Engine | Versions without support | Support ended or ends |
|---|---|---|
| MySQL | 5.7 (amd64 only), 8.0 | 2023-10-31, 2026-04-30 |
| PostgreSQL | 12, 13, 14 | 2024-11-21, 2025-11-13, 2026-11-12 |
| MariaDB | 10.6 | 2026-07-06 |
| MongoDB | 6.0 | 2025-07-31 |

`noust db instance catalog` marks them `(no support)`, and `noust db instance create` says since when they have
had no security fixes and asks you to accept running them: say yes to the question, or give
`--accept-unsupported` (`--yes` alone is not accepting it). The API takes `unsupported_ok` and answers a 400
`confirmation_required` on that field, with the date and a code the console translates, until it is given. They
are never the default, and a prefix still means the supported version it always meant (`--version 8` is 8.4;
8.0 is asked for as `8.0`). A version whose project's date passes while it is still in the catalog needs the same
confirmation, and so does an image of your own that says it is such a version. Redis, Valkey, KeyDB and ClickHouse
publish no end-of-support date for a version, so Noust offers no older one of them.

**Database servers.** The system's engine and the instances are all *database servers*, and
`noust db servers` lists them together: engine and version, where each runs (the system, a container Noust runs,
or a container an application's stack runs), when its support ends, how many databases it holds and which is the
**default** of its engine. A database made with just the engine's name (`noust db create shop --engine mysql`, or
the database a new application asks for) goes to the default. Nobody chose one: it is the system's engine, else the
only instance there is, else the oldest. `noust db servers default mysql@legacy` chooses it, `--clear` takes the
choice back, and a version asked for (`--version`) or a server named by its key (`mysql@legacy`) is always honoured
over it. The console reads the same list from `GET /api/databases/servers`.

```bash
noust db servers                                  # every database server, with its default
noust db servers default mysql@legacy             # new MySQL databases go to the instance
noust db servers default mysql@legacy --clear     # back to the system's engine
noust db adopt shop --engine mysql@legacy         # add one database Noust does not manage yet to Noust
```

A database an engine holds and Noust does not manage is not a kind of database: its state is *not added* and the
action is to add it (`noust db adopt NAME --engine ENGINE`, `POST /api/databases/databases/{engine}/{name}/adopt`).

**KeyDB** is a fork of Redis 6.2 that speaks its protocol and keeps its files, so it is driven by the same
code as Redis and Valkey (keys browser, accounts, profiles, snapshots, metrics, backup policies). It only runs as
an instance: no distribution packages it, so `noust db install keydb` says to make one
(`noust db instance create cache --engine keydb`). Its project has published nothing since 6.3.4 (October 2023),
and publishes no end-of-support date. Noust pins the image the project itself publishes, by digest like the rest;
it is the one image pinned under the tag `latest` (Docker pulls by the digest), because that is the only
multi-architecture tag the project publishes.

**Dragonfly** is not offered yet. What was checked against the real server (`df-v2.0.2`, started in the
container's own Docker by `tests/integration/engines_run.py`, scenario `dragonfly_facts`) decides how it
will be:

- *Snapshots.* By default `SAVE` writes its own format: fifteen `dump-NNNN.dfs` files and a
  `dump-summary.dfs`, which no Redis tool reads and which Noust's one-file copy could not take. Started
  with `--df_snapshot_format=false` it writes one `dump.rdb`, which `redis-check-rdb` reads (four keys
  read of four), which `BGSAVE` produces with the same `INFO persistence` fields Noust waits on
  (`rdb_bgsave_in_progress`, `rdb_last_bgsave_status`, `last_saved_file`), and which a restore (stop,
  replace the file, start) brings back. `redis-cli --rdb` does not work against it (`SYNC` is unknown),
  so a snapshot is the file, not a stream.
- *Accounts.* Dragonfly accepts `ACL SETUSER name on >password ...` and keeps SHA-256 hashes, saved to
  the file `--aclfile` names by `ACL SAVE` and loaded again after a restart (a user with its password
  came back). It **refuses** the form Noust sends to Redis, a password already hashed: `ACL SETUSER name
  on #<sha256>` answers `Unrecognized parameter`. `ACL LIST` and `ACL GETUSER` show only the first
  fifteen characters of each hash. So an account of Dragonfly's is made with the plaintext password on
  the client's standard input (never in a command line), and nothing can read a hash back to compare.
- *What it needs, then.* An image pinned by digest; `--requirepass`, `--aclfile` and
  `--df_snapshot_format=false` in the instance's command; and, in the Redis manager, the plaintext form
  for this flavour. Neither is a guess any more; it is not done in this release.

**ClickHouse** is an analytical column store with its own SQL dialect and access control, and it runs only as an
instance (`noust db instance create events --engine clickhouse --memory 2g`; at least 1 GB, 2 GB suggested). The
versions are its long-term releases. What an instance of it is:

- **Published over HTTP on 127.0.0.1**, the interface applications use (Plausible, Umami, Grafana, the Python and Go
  drivers), on a port above 8123 or the one you give; the native protocol stays inside the container, where Noust's
  own client uses it. A connection string is `http://user:password@localhost:PORT/database`.
- **An administrator Noust generated** (`noust_admin`, with the right to manage accounts, which the image enables
  with `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT`); the image's passwordless `default` user is removed. Its password is
  in the instance's environment file, and the client reads it from there into a file removed when it exits: it is in
  no argument.
- **Databases, accounts and profiles** like the other engines: `noust db create`, `user-create`, `access` with the
  owner / read-write / read-only profiles (a `GRANT` on the database, replaced as a whole), `user-password`.
- **Backups and restores with ClickHouse's own `BACKUP` and `RESTORE`.** The instance is configured with a local
  disk for them inside its data volume; Noust asks the server to write a `.zip` there, copies it out, and deletes it
  from the container whether or not the copy worked. Backup policies, retention, destinations, verification (the
  archive's directory and its `.backup` metadata are read), the restore test (the backup is restored into a
  temporary database and dropped) and the safety copy before a replacement work as for any engine. The database
  inside every backup is called `noust_dump`, so one backup restores under any name; a ClickHouse backup is restored
  into a database that does not exist, so replacing one is `--drop` (after its safety copy). A backup cannot leave
  tables out yet. The server makes the copy table by table from the parts on disk (it is not one snapshot of the
  whole database), so stop what writes if the tables must agree with each other to the row.
- **What it does not do yet:** the SQL console and the data explorer, TLS, engine settings, metrics, moving a database
  to another engine and making a database public. Their tabs do not appear for it in the console.

A ClickHouse container Noust did not make (a Compose stack's) is found and managed for what needs only the
administrator (databases, accounts); backing it up needs the `backups` disk above, and Noust says what to add.

**Variants: PostgreSQL with an extension already installed.** PostGIS and pgvector are files on disk, so an
instance that needs one runs the project's own image:

```bash
noust db instance catalog                     # lists "postgresql --variant pgvector" and "--variant postgis"
noust db instance create vec --engine postgresql --version 17 --variant pgvector --memory 1g
```

A variant is the same engine (the same key `postgresql@vec`, ports, health probe, TLS, dumps and limits) from
another repository, pinned by digest like the rest, with the same majors (15 to 18) and its own tags
(`pgvector/pgvector:pg17`, `postgis/postgis:17-3.5`). `noust db instance upgrade` follows that tag, and a move to
a newer version (`noust db instance migrate`) makes the new instance from the same variant. The PostGIS project
publishes its image for amd64 only: on an arm64 server Noust refuses it, saying so, instead of creating an
instance that cannot start. TimescaleDB and Supabase's PostgreSQL are not offered yet (their images are built on
another base and need their own TLS and role handling, which have not been verified).

A database that uses an extension can only be moved to an engine that has it: `noust db move` reads the
extensions the database has loaded (`pg_extension`) and the ones the target offers, and refuses before dumping
anything when one is missing, naming it.

**Upgrading within a version.** `noust db instance upgrade pg16` asks the registry what image the tag
`16` is *now* and moves the instance to it, keeping the digest it ran. Before touching anything it
dumps every database (a dump that fails stops the upgrade; `--no-backup` skips it), then restarts the
instance on the new image and waits for it to be healthy and to accept its superuser. When it does not,
the previous image is put back and the error carries the container's own log, secrets masked. It does
not change the major version: PostgreSQL 16 stays 16. A new major version is another instance, and
Noust moves the databases for you ([below](databases-move.md#moving-a-database-or-an-engine-to-a-newer-version)).

**A schema on the first start.** PostgreSQL, MySQL and MariaDB run the `.sql` files of
`/docker-entrypoint-initdb.d` once, when their data directory is empty, and Noust can hand them some:

```bash
noust db instance create shop-db --engine postgresql --version 17 --memory 1g \
    --init-script 01-schema.sql --init-script 02-seed.sql
```

The scripts run in name order, once: they are not run again when the instance is restarted, upgraded or
moved, and adding one later changes nothing (load a dump into a running engine with `noust db import`). They
are plain UTF-8 `.sql` files of at most 1 MiB each, ten at most; a shell script is refused, because it would
run inside the engine's own container before the engine is up. They are written beside the Compose file
(`initdb/`, mode 0644) and mounted read-only, and the container has to read them, so **keep secrets out of
them**. Redis, Valkey and MongoDB take none, and say so. The API takes `init_scripts` (`name` and `content`)
on `POST /api/databases/instances` and its `/plan`, and the console's *New instance* has an
*Initialization script* box for the engines that run one. The engine's own free configuration file is not
offered: an engine's settings stay the closed set Noust sizes to the instance's memory.

**An image, a command or arguments of your own.** The catalog's images are the engines' official ones, pinned
by digest by Noust. When you need another (your own build with an extension, a vendor's, a flag the engine needs),
an instance can run it, with the price said and accepted first:

```bash
# Your own image: its registry and tag, which Noust pins by digest when it makes the instance
noust db instance create ts --engine postgresql --version 17 --memory 2g \
    --image ghcr.io/acme/pg-timescale:17.2
# or pinned by you (docker buildx imagetools inspect ghcr.io/acme/pg-timescale:17.2 prints the digest)
noust db instance create ts --engine postgresql --version 17 --memory 2g \
    --image ghcr.io/acme/pg-timescale:17.2@sha256:4f1c...e9
# The catalog's image with arguments of yours after the ones Noust passes the engine
noust db instance create pg --engine postgresql --version 17 --memory 1g --args "-c max_connections=200"
noust db instance launch pg                              # what its container is started with
noust db instance launch pg --args "-c work_mem=16MB" --plan
noust db instance launch pg --args "-c work_mem=16MB"    # asks, restarts the engine
noust db instance launch pg --reset                      # back to what Noust passes; asks for nothing
noust db instance upgrade ts --image ghcr.io/acme/pg-timescale:17.3@sha256:77a0...12
```

- **The image is pinned by digest.** Give its registry and tag (`ghcr.io/acme/pg-timescale:17.2`) and Noust
  pulls the tag when it makes the instance, reads the digest the registry served and records it: the container
  is started from the digest and never from the tag, so what runs is exactly those bytes whatever the registry
  serves for the tag later. A tag is required (`latest` is not assumed), and the plan says that the digest is read
  at creation (`digest_pending` in the API). Give a digest yourself (`repository:tag@sha256:...`) and that digest is
  the one used. `--version` is mandatory with `--image` because Noust cannot read it from an image it did not
  choose, and it decides where the data lives, what TLS the engine can serve and when its support ends. An image
  and a `--variant` are two answers to one question. Noust never looks for a newer image of one you pinned:
  `noust db instance upgrade NAME --image ...` names the next, pinned by its digest, and accepts again.
- **Noust manages it as it manages an official one:** the data where the official image keeps it, the engine's
  own port, the superuser's password in the variable the official image reads, the health probe, and dumps and
  restores through the engine's own client inside the container. An image that does any of that differently will
  not come up, or will not be managed correctly, and the plan says so. A private registry needs `docker login` on
  the server.
- **Arguments follow Noust's** (`postgres -c shared_buffers=... YOURS`), so where the engine lets the last flag
  win, yours win. A **command** is the container's whole command: Noust adds nothing to it (the memory share it
  sizes the engine with, the TLS arguments), so it replaces arguments and arguments replace it, and it is refused
  for an instance that serves TLS. Words are an argument vector, split like a shell would and never given to one;
  a quote that never closes is a usage error. None can carry a secret: `--requirepass` and its kin are refused,
  because a command line is visible to every local user and the instance's password is in a file only root reads.
- **The price is accepted in words.** A plan carries the warning, and making one without accepting it is refused
  with every warning (interactively, saying yes to the question is accepting it; `--yes` and `--json` alone are
  not, a script says `--accept-untested`; the API takes `accept_untested` and answers a 400
  `confirmation_required` carrying the warnings). Taking the words back (`launch --reset`) asks for nothing.
- **A change is a restart behind the same readiness gate** a creation and a change of limits pass: when the
  engine does not come up on the new words, the previous ones are put back, the engine is started on them and the
  error carries the container's log. The words are the instance's row, never an edited file; the audit trail
  records `db.instance.create` with variant `custom` and `db.instance.launch` with what changed, never the words.
- **Discovery.** A container is a database by what Noust wrote on it (`io.noust.managed`, `io.noust.db-instance`
  and `io.noust.db-flavour` together) and not only by the name of its image, so an image of yours with any name is
  listed, bound and managed like the rest. A major-version move (`noust db instance migrate`) refuses an instance
  on an image of yours and says how to do it by hand (make a new instance from the newer image and `noust db
  move` the databases to it); it makes the new instance without your arguments and says so, because a flag one
  version takes another may not.

**Limits and health.** An instance is held to a memory limit from the day it is made, and the other
two limits a container can have are settings too, as is the probe that decides whether the engine is
well:

```bash
noust db instance limits pg16                                  # what it is held to, and how it is doing
noust db instance limits pg16 --memory 2g --cpus 1.5 --plan    # what the change would do
noust db instance limits pg16 --memory 2g --cpus 1.5           # asks, restarts the engine
noust db instance limits pg16 --cpus none --swap default       # put the defaults back
noust db instance health pg16 --interval 15 --timeout 5 --retries 5
```

The memory is checked against what the other instances leave of the share instances may hold
(its own memory counts as available to it), the CPUs against the server's (Docker refuses more than
the server has), and a probe whose timeout is longer than its interval is refused: Docker would call a
healthy engine unhealthy. `--swap 0` gives the container no swap at all (Compose counts memory and swap
together, so Noust writes a `memswap_limit` equal to the memory); without `--swap` the container has
Docker's default. The engine's caches are sized to the new memory when it restarts. A change is a
restart behind the same readiness gate a creation passes: when the engine does not come up on the new
numbers, the previous row and Compose file are put back, the engine is started on them and the error
carries the container's log. `noust db instance health` without options shows what Docker says of the
engine now and the output of the last probe, verbatim. Limits and probe are the instance's row (a
validated column each), never an edited file. Applying them to the host's own engines (a systemd
drop-in) is not part of this release.

**Removing.** `noust db instance remove pg16` lists what it deletes (the databases, the data volume)
and asks; `--yes` is the answer in a script. It refuses, whatever you confirm, while an application
uses a database on it (unlink it first) or one of its databases is public (close it first). Backup
policies of its databases are removed with it; dumps already taken stay where they are. A removal that
could not finish names what is left, and running it again finishes it. A creation that fails removes
what it made, and says why with the container's own words.

**Interrupted, or half made.** A row says how far creating, upgrading or removing got (`state` in
`noust db instance list --json`). An instance that is not `ready` is listed but offered to nobody: no
application is given a database on it. `noust db instance remove NAME` cleans one up.

**What an instance needs.** Docker with its Compose plugin (`docker compose version`), root, and
`systemd`. It is not available on a hub. Under the `ens-medium` profile making a database public is
refused, an instance's included.

**What it does not do yet.** The engine's own settings are the ones above (`noust db settings` is for
the host's engines, and says so for an instance). An instance can serve TLS ([below](databases-tls.md#tls-for-an-instance-and-for-a-link)); Redis, Valkey and KeyDB
serve it as `required` only, and ClickHouse cannot yet.

The API is under `/api/databases/instances`: `GET` the list, `GET .../catalog`, `POST .../plan` (what a
creation would do, changing nothing), `POST` to make one (sudo mode, a job), `GET .../{name}`,
`POST .../{name}/start` (a job), `POST .../{name}/stop`, `POST .../{name}/upgrade` (sudo mode, a job),
`GET .../{name}/remove-plan` and `DELETE .../{name}?confirm=true` (sudo mode, a job),
`GET .../{name}/tuning`, `POST .../{name}/tuning/plan` and `PUT .../{name}/tuning` (sudo mode, a job),
`GET .../{name}/launch`, `POST .../{name}/launch/plan` and `PUT .../{name}/launch` (sudo mode, a job; `command`,
`args` as lists of words, `reset`, `accept_untested`). `image`, `command`, `args` and `accept_untested` are also
fields of `POST .../plan` and `POST` to make one, and `POST .../{name}/upgrade` takes a body with `image` and
`accept_untested` for an instance on an image of yours. They need
`databases.read`, `databases.manage` (and `apps.operate` for start and stop), and are the server's: a
team member does not see them, but picks one for its application's database through
`GET /api/databases/provisioning/choices`.
