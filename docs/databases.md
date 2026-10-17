# Databases

Noust manages the databases of the server it runs on: the engines installed on the host, the
database containers Docker runs (from 3.3), and, from 3.4, the **instances** it runs itself: another
version of an engine, or one the host does not provide, in a container of its own. The console's
Databases area and `noust db` are two faces of the same code.

## Where a database lives

| Key | What it is |
|---|---|
| `postgresql`, `mysql`, `redis`, `mongodb` | the engine installed on the host |
| `postgresql@<name>` (any engine) | an instance Noust runs (`noust db instance`) |
| `postgresql@<project>.<service>` | a service of a Docker Compose project |
| `postgresql@<container>` | a container without a Compose project |

Containers are found on their own from their images (official PostgreSQL, MySQL, MariaDB, Redis,
Valkey and MongoDB images and their common derivatives). The key goes wherever an engine name
went:

```bash
noust db engines                                  # host engines, then containers
noust db list                                     # every database, and the engines that could not be read
noust db tables proggest --engine postgresql@proggest.postgres
noust db backup proggest --engine postgresql@proggest.postgres
```

A container's databases belong to the application its Compose project is. Noust reads the
container's own credentials inside it (`POSTGRES_USER`, `MYSQL_ROOT_PASSWORD`, `*_FILE`,
`--requirepass`...), never on the host's command line. It does not install, uninstall or
configure an engine inside a container it did not make: that is the image's and the compose
file's. An instance is the exception: Noust made it, so Noust makes, upgrades and removes it (next
sections).

## When Noust cannot get in

A listing that fails is never shown as an empty engine. The engine's answer is shown verbatim
with the fix. For MySQL/MariaDB and Redis, store the account Noust signs in with; the engine is
asked first and nothing is saved if it refuses:

```bash
noust db config --engine mysql --user root --password    # asks for the password without echo
```

PostgreSQL is reached as the system's `postgres` user (peer authentication), and MongoDB as the
`noust_admin` administrator Noust created; their messages say what to restore.

## Which application uses which database

Besides the links Noust writes (`noust db link`), Noust reads each application's environment and
shows the databases it already names as **detected**: connection URLs (`DATABASE_URL`,
`REDIS_URL`...) and the `DB_*`/`REDIS_*` sets, resolved to one engine by port or by Compose
service. Recording one (the console's "Record the link") makes it a link without touching the
application. It is always one use at a time, asked for by the operator, because an application's
`.env` is written by whoever deploys it: Noust refuses to record a use of a database another
application owns, never overwrites an existing link, and records the link without an account, so a
password rotation (`noust db user-password`) does not write the new password into that
application. When Noust should manage the application's connection string, link it with
`noust db link` instead.

## SQLite files

Most self-hosted applications (Vaultwarden, Memos, PocketBase, Uptime Kuma, a Django project left on
its default) keep their data in a SQLite file inside their own directory. It is not an engine Noust
installs, but it is a database the application depends on, so the application's Database tab and
`noust db sqlite` show it:

```bash
noust db sqlite list                          # every application's files
noust db sqlite list vault.example.com        # one application's, with size, journal mode and the log waiting
noust db sqlite check vault.example.com data/db.sqlite3          # SQLite's quick_check, without closing it
noust db sqlite check vault.example.com data/db.sqlite3 --full   # integrity_check: every index against its table
noust db sqlite copy vault.example.com data/db.sqlite3 -o /root/before-upgrade.sqlite
```

A file is found by its header, wherever it is in the data directory: in `shared/` for an application
on releases (the releases themselves hold code), in the application's directory in place. Links are
not followed and dependency folders (`node_modules`, `.venv`, `.git`) are not entered. A database
stays open for the application the whole time: a check reads it read-only, and a database in the
rollback journal mode accepts no write while SQLite reads it, so run a thorough check of a large one
in a quiet moment. The console refuses to check a file over a gigabyte (the request would wait on the
disk); the terminal has no limit.

`copy` is the same SQLite backup API every application backup uses: it holds every committed
transaction, the ones still in the write-ahead log included, and none half-written. The file is
created private (0600), an existing file is never replaced, and it is **not** offered over HTTP: it
would put the application's whole data in a response. Backups of the application already include a
consistent copy of every file listed.

Applications that run in Docker Compose keep their SQLite files in bind mounts and volumes: the
bind-mounted ones under the project directory are listed; volumes are covered by the volume backups,
which take each SQLite file in them whole as well.

## Installing an engine

```bash
noust db catalog                                  # what this server can install, and why not
noust db install postgresql --version 17          # from the PostgreSQL project's repository
noust db install mariadb --version 11.4
noust db install mysql --version 8.4              # Oracle's long-term series, on Debian too
noust db install valkey
```

Upstream repositories are added with their signing keys pinned by fingerprint. MySQL and MariaDB,
and Redis and Valkey, are never installed beside each other.

**Rocky Linux, AlmaLinux, Red Hat Enterprise Linux and openSUSE Leap.** Noust installs an engine on the
host with `dnf` on the RHEL family, major 9 (Rocky Linux, AlmaLinux, Red Hat Enterprise Linux, CentOS
Stream and Oracle Linux, which are rebuilds of the same sources), and with `zypper` on openSUSE Leap 16.0
and 15.6. The tables were read from the real repositories and every cell was installed, used and removed
in a container of that release (`tests/integration/engines_run.py`, scenarios `rpm_el9`, `rpm_leap16` and
`rpm_leap156`):

| Engine | RHEL family 9 | openSUSE Leap 16.0 / 15.6 |
|---|---|---|
| PostgreSQL | 13 (the plain package), 15, 16, 18 (AppStream module streams), 14 and 17 (PGDG) | 14 to 18, from the distribution |
| MariaDB | 10.5 (plain), 10.11, 11.8 (module streams), 11.4 (MariaDB's repository) | 11.8 / 10.11 |
| MySQL | 8.0 (plain), 8.4 (module stream) | not in any Leap repository: an instance |
| Redis | 6.2 (plain), 7.2 (module stream) | needs a configuration file first: an instance |
| Valkey | 8.0 | an instance |
| MongoDB | 7.0, 8.0 (MongoDB's repository) | not in any Leap repository: an instance |

A repository Noust adds (PGDG, MariaDB, MongoDB) is a `.repo` file of its own (`noust-<name>.repo`, with
the "Generated by Noust" mark) whose key is the file Noust verified: it downloads the key, compares its
fingerprint with the one pinned in the source and only then writes it where `dnf` or `zypper` trusts it,
exactly as for apt. A repository the package manager then cannot read is taken back out. `noust db
uninstall` removes the packages that are exactly the engine's (never a pattern) and the repository Noust
added for it.

A release, an engine or a version that is not in the tables is refused before anything is touched, and the
refusal names the two ways that do work: an **instance**, an engine in a container that needs no package
manager and runs the same on any host with Docker (`noust db instance create NAME --engine postgresql`), or
the distribution's own tool (`dnf install postgresql-server`, `zypper install mariadb`), after which Noust
manages the engine it finds: users, databases, dumps and restores. Fedora, the next RHEL major and
Tumbleweed are not in the tables yet. On the RHEL family and openSUSE an engine's settings and resource
limits assume the Debian layout and say so when they do not find it.

MySQL comes from the distribution where the release ships it (Ubuntu) and from Oracle's own APT
repository (`repo.mysql.com`) where it does not (Debian) or when a long-term series the release does
not ship is asked for: `8.4` and `9.7` on Debian 12 and 13 and on Ubuntu 22.04, 24.04 and 26.04.
Oracle publishes for amd64 only, so on an arm64 server Noust says so and points at an instance
(`noust db instance create NAME --engine mysql`), whose official image is built for both
processors. 8.0 is not offered from Oracle: it reached the end of its support in April 2026.

## Instances: another version, or an engine the host lacks

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
  (`noust db public`, below), which forwards a port you choose to the instance's loopback port.
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
Noust moves the databases for you ([below](#moving-a-database-or-an-engine-to-a-newer-version)).

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
the host's engines, and says so for an instance). An instance can serve TLS ([below](#tls-for-an-instance-and-for-a-link)); Redis and Valkey
cannot yet.

The API is under `/api/databases/instances`: `GET` the list, `GET .../catalog`, `POST .../plan` (what a
creation would do, changing nothing), `POST` to make one (sudo mode, a job), `GET .../{name}`,
`POST .../{name}/start` (a job), `POST .../{name}/stop`, `POST .../{name}/upgrade` (sudo mode, a job),
`GET .../{name}/remove-plan` and `DELETE .../{name}?confirm=true` (sudo mode, a job),
`GET .../{name}/tuning`, `POST .../{name}/tuning/plan` and `PUT .../{name}/tuning` (sudo mode, a job). They need
`databases.read`, `databases.manage` (and `apps.operate` for start and stop), and are the server's: a
team member does not see them, but picks one for its application's database through
`GET /api/databases/provisioning/choices`.

## PostgreSQL extensions

An extension is loaded into **one database** with `CREATE EXTENSION`; which ones can be loaded depends on
what the server has on disk (the host's packages, an instance's image), so Noust reads the server's own list
(`pg_available_extensions`) and never keeps a catalogue of its own.

```bash
noust db extension list shop_db -e postgresql            # what is loaded
noust db extension list shop_db -e postgresql --all      # everything the server offers, trusted or not
noust db extension add shop_db vector -e postgresql@vec
noust db extension add shop_db postgis_topology -e postgresql@geo --cascade   # and the extensions it needs
noust db extension remove shop_db vector -e postgresql@vec                    # asks; --cascade drops what depends on it
```

- **The name is the server's.** What is asked for must be a name the list returns, and it reaches the statement
  quoted as an identifier; an extension the server does not offer is refused, saying where one comes from (on the
  host, the extension's package first: `apt install postgresql-17-<extension>`; on an instance, an image that has
  it). Nothing is sent to PostgreSQL before that.
- **It runs as the cluster's superuser** and is recorded in the audit trail (`db.extension`). Most extensions that
  matter are not *trusted* (PostgreSQL lets a database owner load only the ones whose control file says so), and
  loading one runs its script with the superuser's rights: the API therefore asks for the whole server's scope for
  an extension that is not trusted, and a team member loads only the trusted ones.
- **PostgreSQL only,** and never in PostgreSQL's own databases (`postgres`, `template0`, `template1`). Removing
  an extension without `--cascade` is refused by PostgreSQL while anything depends on it, and says what.

The console shows the loaded extensions on the database's Overview and loads or unloads one from there. The API
is `GET /api/databases/databases/{engine}/{name}/extensions`, `POST` to the same path (`name`, `cascade`) and
`DELETE .../extensions/{extension}?cascade=` (sudo mode), with `databases.read` and `databases.manage`.

## A dump from elsewhere, and a whole server's databases

A dump is a script. `COPY ... FROM PROGRAM` runs a program for whoever loads it, and a superuser's
session runs it as the `postgres` account. So Noust does not give a dump the superuser unless you say
so.

```bash
noust db import shop /srv/dumps/shop.sql.gz -e postgresql          # as the database's owner
noust db import shop /srv/dumps/shop.sql.gz -e postgresql --as-superuser     # the explicit way
noust db restore shop /srv/dumps/shop.sql.gz -e postgresql --as-superuser    # a file, not a dump of Noust's
```

- **PostgreSQL loads a dump from outside as the database's owner.** The load signs in as a role made for
  it (not a superuser, member of the owner, `VALID UNTIL` soon, dropped when it is done or swept by the
  next load if the process died), so the server itself refuses what the owner may not do: `COPY ...
  FROM PROGRAM`, an extension that needs a superuser, reading a server file. A script cannot hand the
  superuser back from inside (`RESET ROLE` and `SET SESSION AUTHORIZATION` lead back to that role, not
  to `postgres`). A database owned by the superuser (one made without an owner) is loaded by a login
  that owns what it makes and hands it to the real owner afterwards. It signs in over `127.0.0.1` like
  the read-only console, so `pg_hba.conf` needs the line the distribution's default has (`host all all
  127.0.0.1/32 scram-sha-256`); when it does not, the error says so and says how to repeat the load
  with `--as-superuser`.
- **A refusal for want of rights says how to repeat it.** The server's own words come first, then the
  way to load it as the superuser. `--as-superuser` asks twice on the command line; in the API it is
  `as_superuser` with `confirm_superuser`, and sudo mode. It is on the audit trail
  (`db.restore.superuser`). Use it for a dump you made or have read. With it no `SET ROLE` is written
  into the script, so what the dump creates belongs to the superuser unless it names owners
  (`--keep-owners`) or you run `noust db fix-owner` afterwards.
- **Noust's own dumps go back as they were.** A backup restored by name, a download from a destination, the
  safety copy put back after a failed restore, a database moved between engines and an application
  backup's database are loaded as the superuser, because what is in them is what Noust took out of the
  database (extensions that need a superuser, objects of several owners). So is a dump that sits in
  the engine's own folder when you name it with `noust db restore`.
- **MySQL, MariaDB and MongoDB are loaded as the administrative account.** Their dumps are checked for
  the statements that leave the database (`USE`, `CREATE DATABASE`) but run with that account's rights;
  `--as-superuser` is refused for them. Import only dumps you trust into those engines.

### Every database of a server, and its accounts

`pg_dumpall` writes the accounts and every database in one file. `noust db import-all` brings it in:

```bash
noust db import-all /srv/dumps/all.sql.gz -e postgresql --plan            # what would happen
noust db import-all /srv/dumps/all.sql.gz -e postgresql                   # the databases
noust db import-all /srv/dumps/all.sql.gz -e postgresql --users           # and the accounts
noust db import-all /srv/dumps/all.sql.gz -e postgresql --users --replace -d shop
```

- **Read, never run.** The file is cut where psql would read each `\connect` (a row, a string or a
  function body that looks like one is data). The accounts become data and the statements that create
  them are written by Noust from it, so a role named `x; DROP ...` is only a name that is not used.
  Each database's part is an ordinary dump that goes through the import above, with its own check, its
  own safety copy and its own way back.
- **The accounts only when you ask** (`--users`, `restore_users` in the API), after the plan shows them
  and you accept. They keep the password hash the dump holds (the old password signs in), but not the
  power the source gave them: superuser, `CREATEROLE`, replication and row-security bypass are left out
  and listed. An account that exists is never touched, `postgres` and the server's own roles are not
  created, and a membership is kept only between accounts made now. Per-role settings, tablespaces and
  `DROP` statements are listed as not carried.
- **Owners and privileges** are kept for the roles that exist and left out, statement by statement, for
  the rest (the source's `postgres`): a database that is created is owned by the account that owned it
  at the source, one that is replaced is recreated with it, and one that is loaded over is handed to it
  afterwards. Objects owned by another account than the database's go to the database's owner: only the
  owner can be handed an object by a loader that is not a superuser. `--as-superuser` keeps them all.
- **Databases fail alone.** One that does not load is told with the engine's own words and dropped again
  if this created it; the others load, and the exit status is 1.
- **The server's own databases** (`postgres`, `template1`) are loaded only when named (`-d postgres`).

The API is `POST /api/databases/imports/cluster/plan` (reads the file, changes nothing) and
`POST /api/databases/imports/cluster` (a job with sudo mode; `restore_users` needs `confirm_users`).
The upload and the path rules are the single import's, and a file that holds one database is refused with
where to go.

**MySQL and MariaDB.** `mysqldump --all-databases` (or `--databases`) is read the same way: each
user database becomes a file of its own, both of mysqldump's passes included (a view is written twice,
under a second `-- Current Database` marker), without the `CREATE DATABASE` and `USE` that name it, with
the header the dump set up and the footer it restored; the database is made with the character set the
dump gave it. The server's own schemas (`mysql`, `sys`) are never loaded: `mysql` holds the server's
accounts and grants and loading it would overwrite them, root included. The accounts come from a file
of their own, because a `mysqldump` carries them as table data, not statements: `--accounts-file`
(`accounts_upload_id` or `accounts_path` in the API) takes the `CREATE USER` and `GRANT` lines that
`SHOW CREATE USER` and `SHOW GRANTS` print (or `pt-show-grants`), one per line. They are read into data
and written again by Noust: a password only as the `mysql_native_password` hash the server stores (the
old password signs in), privileges only on a database or a table and only those about its data (never
`FILE`, `SUPER`, `PROCESS`, `RELOAD`, `CREATE USER`, replication, a global grant or `WITH GRANT OPTION`:
they are listed as not carried), `ACCOUNT LOCK` kept. A `caching_sha2_password` hash (MySQL 8.4's
default) holds raw bytes a text file cannot carry exactly, so such an account is made **locked**, for you
to give a password with `noust db user-password`. Accounts are made first and their privileges after the
databases are loaded, because a grant on a table needs the table; one that exists is never touched.
Unlike PostgreSQL, a MySQL or MariaDB dump is loaded with the administrative account (its views,
triggers and routines are written with a `DEFINER` of the source's accounts, which an unprivileged login
could not create), so import only dumps you trust into them.

Tested against a real PostgreSQL 16 (`tests/integration/engines_run.py`, scenarios
`restore_unprivileged` and `import_cluster_postgresql`): the server refuses `COPY ... FROM PROGRAM`,
`RESET ROLE` and `SET SESSION AUTHORIZATION`, nothing runs and the database is put back, an ordinary
dump's objects belong to the owner, an extension that needs a superuser needs `--as-superuser`, a
dump of all databases brings the accounts with the passwords they had and none a superuser, a hostile
database of the file fails alone, and no login of the loader is left.

## Moving a database, or an engine to a newer version

Two commands move data between engines, and both leave the engine they move away from exactly as it
was, so whatever goes wrong can be put back.

```bash
noust db move shop_db --from postgresql --to postgresql@pg17 --plan   # what it would do, nothing else
noust db move shop_db --from postgresql --to postgresql@pg17          # asks, then does it
noust db move shop_db --from postgresql@pg16 --to postgresql --drop-source   # and drops it there, after a last dump

noust db instance migrate postgresql --to-version 17 --plan   # every database of an engine, into a new instance
noust db instance migrate pg16 --to-version 18 --name pg18 --memory 2g
noust db instance migrations                                  # the moves, newest first, and which can be undone
noust db instance rollback 3                                  # the applications go back to the engine they left
```

**One database (`noust db move`).** It dumps the database, makes its accounts on the target with the
passwords Noust keeps (an account whose password Noust never knew is made with a new one, named in the
output and revealed with `noust db user-password --show`), loads the dump, gives each account its
profile again, and points every application that uses it at the target, behind that application's
health gate. The accounts arrive before the data, so an application's connection string changes in its
host and port only. A backup policy follows the database. When an application does not come up on the
target, its variables, those of every application changed before it and what the move made on the target
are put back. The source keeps the database unless you ask for `--drop-source`, which takes a last dump
first. `--replace` replaces a database of that name the target already holds, after a safety copy.

**Every database of an engine (`noust db instance migrate`).** SOURCE is an instance (`pg16`) or the
host's engine (`postgresql`). Noust makes a new instance of the newer version (`--to-version`, one the
catalog offers), moves each database into it as above, and stops the old instance (the host's engine is
left running). A failure at any point removes the new instance and puts the applications back. The
version must be newer than the one it leaves, and a move is recorded: `noust db instance migrations`
lists them, and `noust db instance rollback N` points the applications back at the engine they left.
What was written to the new instance since the move is dumped first and is *not* carried back: an older
server cannot load a newer one's dump. The new instance is stopped, not removed; removing the old one
is yours to do once the new one has proved itself.

What stops a move, listed before anything changes: a database made public (close it first), a
variable Noust did not write (it will not rewrite what it cannot restore), an account an application
signs in as whose password Noust does not know, an older target server (it could not read the dump),
a name the target already holds, an engine that is not PostgreSQL, MySQL, MariaDB or MongoDB (a Redis
or Valkey snapshot is the whole instance), and an application that connects with TLS to a target that
cannot keep it. `--plan` and `--dry-run` change nothing; the dump is the one thing a move cannot make
atomic, so writes made to the database while it runs are not carried over: stop what writes for the
length of it.

The console does the same: the database's menu says *Move to another engine*, an instance's says *Move
to a newer version*, and the Engines page lists the moves with the way back. The API is
`POST /api/databases/moves/plan` and `POST /api/databases/moves`,
`POST /api/databases/instances/migrate/plan` and `POST /api/databases/instances/migrate` (sudo mode, jobs),
`GET /api/databases/instances/migrations` and `POST .../migrations/{id}/rollback` (sudo mode, a job).

### To another server of the fleet

`noust db move` stays on one server. To take a database to another server of the fleet, from the central, use
`noust db transfer shop_db --from db-1 --to db-2` (add `--accounts` for the accounts that had access to it,
`--drop-source` to drop it from the first server after a last dump). The source dumps it, the central carries
the dump in chunks that resume and keeps none of it, and the target loads it with its own import, as the
database's owner and never as the superuser. How it works, what is checked before anything is dumped and what
becomes of the accounts' passwords: [CENTRAL.md](CENTRAL.md#moving-a-database-to-another-server).

## TLS for an instance, and for a link

An instance can serve TLS with a certificate signed by an authority of Noust's own, and an application
can be told to verify it.

```bash
noust db instance tls pg16                           # how it serves TLS now, and its certificate
noust db instance tls pg16 --mode optional --plan    # what the restart would do
noust db instance tls pg16 --mode required           # asks, restarts the engine on TLS
noust db instance renew-tls pg16                     # a new certificate
noust db instance tls-ca                             # the authority applications trust
noust db link shop.example.com shop_db --engine postgresql@pg16 --tls verify-full
```

- **Modes.** `off`; `optional` (a client that asks for TLS gets it); `required` (nothing else gets in:
  PostgreSQL refuses a connection that is not TLS, MySQL and MariaDB set `require_secure_transport`).
  Noust's own client inside the container keeps connecting through the engine's local socket, so
  `required` never locks Noust out. MongoDB serves TLS to those who ask (`optional`) and cannot refuse
  the rest; MariaDB can refuse plaintext from 11.4. Redis, Valkey and KeyDB serve TLS on a port of
  their own, so Noust gives them one mode, `required`: the instance's one published port speaks TLS and
  the plaintext port is shut (`optional` would need a second published port, and is refused with that
  reason). Noust's own client inside the container, its health check, the key browser and the backups
  are told to speak it, so everything goes on working; a link takes a TLS mode and the application
  gets a `rediss://` URL with redis-py's `ssl_cert_reqs`, `ssl_ca_certs` and `ssl_check_hostname`
  (`DB_SSL_MODE` and `DB_SSL_CA` with `--extra-vars`). A link without a mode to such an instance is
  refused: its URL could not connect. Making such a database public no longer asks for
  `--allow-plaintext`, because the port is TLS-only already. A mode the engine cannot serve is refused,
  naming why, before anything changes. ClickHouse is not offered yet.
- **The authority.** The first instance that serves TLS makes it: a P-256 key and a certificate in
  `/etc/noust/tls` (the key readable by root only). The certificate of an instance carries the names it
  is reached by (`localhost`, `127.0.0.1`, `::1`, its name, its container name and the server's
  hostname) and is valid for 825 days. Noust says on `noust db instance tls` and in the console when it
  expires, and `renew-tls` signs it again. Every key is written by Noust, through its filesystem seam,
  and is never on a command line.
- **Changing the mode restarts the engine** and waits for it to answer. When it does not, the previous
  configuration is put back, the instance runs as it did, and the error carries the container's own
  log. `--plan` lists the applications that lose their connection for the length of the restart.
- **A link may say how the application treats the certificate** (`--tls`, or *Connection security* in
  the console): `require` encrypts and checks nothing about who answers; `verify-ca` also checks that
  the authority signed it; `verify-full` also checks the name. Noust writes the client library's own
  words into the connection string (`sslmode=verify-full&sslrootcert=/etc/noust/tls/ca.crt` for
  PostgreSQL, `ssl-mode=VERIFY_IDENTITY&ssl-ca=...` for MySQL and MariaDB, `tls=true&tlsCAFile=...` for
  MongoDB) and, with `--extra-vars`, in `DB_SSL_MODE` and `DB_SSL_CA`. Only the authority's *public*
  certificate is handed over. The host's own engine can be asked to `require` and nothing more, because
  its certificate is its package's and nothing can verify it; to verify, move the database to an
  instance (`noust db move`). A link to an instance that does not serve TLS is refused with the command
  that turns it on.
- **It follows the database.** A move or a migration keeps a link's mode, refuses a target that cannot
  keep it, and a migration from the host's engine makes the new instance serve TLS (`optional`) when an
  application connects with it. A migration from an instance that serves TLS makes one that serves it
  in the same mode.

The API is `GET /api/databases/instances/{name}/tls`, `POST .../tls/plan`, `PUT .../tls` and
`POST .../tls/renew` (sudo mode, jobs), `GET /api/databases/instances/tls-authority`, and `tls` in the
body of `POST /api/apps/{domain}/databases/link`. The routes are the server's and need `databases.read`
or `databases.manage`.

## A database on the engine and version you choose

```bash
noust db choices                                  # what a database can be created on, and how
noust db provision shop.example.com -e postgresql --version 17 --install
noust db provision shop.example.com -e postgresql --version 16        # on an instance that runs 16
noust db provision shop.example.com -e postgresql --instance pg16
noust create -d shop.example.com -s github:me/shop --database postgresql:shop --database-version 17
noust create -d shop.example.com -s github:me/shop --database postgresql:shop --database-instance pg16
```

`--version` is numbers and dots (`17`, `11.4`) and matches by components: `17` is any 17.x, and
`11` is not 10.11. Without it nothing changes: the database is created on the engine that is
installed. The host's engine is the primary one, so:

- the version that runs is used;
- another version than the one that runs is answered by the instance that runs it, by itself when
  exactly one does, and by name with `--instance` (`instance` in the API) when several do or you
  want to say which. With none, it is refused with what runs (`PostgreSQL 17 was asked for, but this
  server runs PostgreSQL 16.4`) and the command that makes an instance of it, before anything is
  created. An instance is never made as a side effect;
- MariaDB is not MySQL and Valkey is not Redis: asking for the one that is not installed on the host
  is answered by an instance of it, or refused the same way;
- an engine that is not installed is installed first only with `--install` (`install: true` in the
  API, which needs `databases.manage` and sudo mode, the install's own), from the catalog
  (`noust db catalog`), pinned keys and all; without it the refusal names `noust db install`.
  A version the release cannot have lists the ones it can.

The API: `GET /api/databases/provisioning/choices` lists each flavour with the version that runs and
each version as `installed`, `installable` or `unavailable`; `GET
/api/databases/provisioning/plan?domain=...&engine=...&version=17` says what would be written and
whether the engine would be installed (`will_install`); `POST /api/apps/{domain}/databases` takes
`version` and `install`, and `POST /api/apps` takes `database.version`, checked when the request
arrives.

## Settings

```bash
noust db settings postgresql                      # current, recommended, restart needed
noust db settings postgresql shared_buffers=2GB work_mem=64MB
noust db settings mysql max_connections=default   # back to the engine's own default
```

Each engine has a closed set of settings. They are written to Noust's own file
(`conf.d/90-noust.conf` for PostgreSQL, `99-noust.cnf` for MySQL and MariaDB,
`/etc/redis/noust.conf` for Redis and Valkey, and MongoDB's YAML), checked with the engine's own
tool, applied, and put back if the engine does not come back, or if the change is interrupted half
way. One change per engine runs at a time. A slow start (a large Redis data set loading, MySQL
recovering after a crash) is waited for up to 15 minutes while the engine says it is still
starting, rather than rolled back.

A change that costs something is refused until it is confirmed (`--yes`, or `confirm` in the API,
whose refusal lists every warning in `warnings`); nothing is written before that:

- listening beyond the loopback, including removing a listen setting (`listen_addresses=default`)
  when what the engine falls back to is not known to be the loopback. Under the `ens-medium`
  profile it is refused outright, and an engine that turns out to listen beyond the loopback once
  it restarts is put back on its previous settings;
- a Redis `maxmemory` below what Redis holds now: with `noeviction` writes start failing, with any
  other policy keys are dropped at once;
- `appendonly no` or `save off` on Redis.

Redis writes a snapshot (`BGSAVE`, waited for) before Noust restarts it, and the restart does not
happen when the snapshot fails. Cache sizes (`shared_buffers`, `innodb_buffer_pool_size`,
`maxmemory`) stop at 90% of the server's memory. A configuration file or directory that is a
symbolic link is refused: Noust writes as root, and the engine's own account owns some of those
directories. On a server with several PostgreSQL clusters running, Noust does not guess which one
to configure and says so; every step targets the one cluster by name.

## Resource limits of an engine on the host

An application has limits behind the health gate and an instance has them from the day it is made
(`noust db instance limits`). The engine the distribution installed on the host can be held to the
same four, so one runaway query does not take the memory of every application beside it:

```bash
noust db limits postgresql                                    # what Noust sets, what systemd holds, what the kernel enforces
noust db limits postgresql --memory 4g --cpus 2               # memory, processors
noust db limits postgresql --swap 0 --tasks 4096              # no swap; processes and threads
noust db limits postgresql --remove cpus                      # take one away
```

They are written to a systemd drop-in of Noust's own beside the engine's unit
(`/etc/systemd/system/<unit>.service.d/90-noust-limits.conf`: `MemoryMax=`, `CPUQuota=`,
`MemorySwapMax=`, `TasksMax=`), never to the distribution's unit file. `daemon-reload` makes systemd
write them into the running service's cgroup, so the engine is **not restarted** and no connection
is dropped. A limit is then proved twice instead of assumed: systemd is asked what it holds for the
unit, and the kernel's cgroup files are read for what it will enforce. A limit systemd accepts and
the kernel ignores (a cgroup v1 host, or memory accounting off) is reported as such. If systemd
does not hold the limit as written (another drop-in sets it later), refuses the reload, or the
engine stops answering, the previous drop-in is put back, systemd reloads it again, and the error
carries the engine's journal verbatim.

A memory limit is a promise the kernel keeps by killing the engine's process when it is exceeded;
for a database that is a crash and a recovery. Set it well above the engine's cache settings
(`shared_buffers`, `innodb_buffer_pool_size`, `maxmemory`); one below what the engine uses now is
refused until it is confirmed (`--yes`, or `confirm` in the API). The least memory accepted is what
the engine needs to start, and nothing above the server's own memory or processors is.

The limits belong to the unit, not to a database: every database of the engine shares them. An
engine in a container has its own (`noust db instance limits`). The console shows the same form
under an engine's settings, and `GET`/`PUT /api/databases/engines/{engine}/limits` is the API
(sudo mode to change, audited as `db.limits.change`).

## Recovering PostgreSQL to an instant

A dump says what a database held when it was taken. Point-in-time recovery says what the whole
cluster held at any instant since: PostgreSQL copies every segment of its write-ahead log to an
archive as it fills, Noust takes a base backup now and then, and a recovery puts a base backup back
and replays the log over it up to the instant you name. It is the engine's own mechanism
(`archive_command`, `pg_basebackup`, `recovery.signal`, `recovery_target_time`) driven by Noust, with
no external archiver: a server that loses Noust can still recover by hand from the same files.

```bash
noust db pitr enable --plan                    # what the engine is given, and that it restarts once
noust db pitr enable --keep-days 14 --to nas   # turn it on, prove the archive, take the first base backup
noust db pitr status                           # the window that can be recovered, the base backups, warnings
noust db pitr restore --time '2026-10-17 12:34:56' --plan
noust db pitr restore --time '2026-10-17 12:34:56'      # asks first
noust db pitr restore --latest                 # to the end of the archived log
noust db pitr base                             # a base backup now (the timer takes one weekly)
noust db pitr fetch nas                        # bring the archive back on a server that lost its own
noust db pitr disable                          # settings back; --delete-archive also removes the files
```

- **What it asks of the engine.** `archive_mode = on` (one restart, which it asks about),
  `archive_command`, `archive_timeout = 60s` (so a quiet server still archives every minute, which is
  the granularity of a recovery) and a `wal_level` of at least `replica`, set with `ALTER SYSTEM` so
  they work on any layout and undo with `RESET`. An `archive_command` that is not Noust's is never
  replaced. The command is coreutils alone (copy, flush, rename into place; a segment archived twice
  with the same bytes succeeds, one that differs fails loudly), so the archive keeps working when
  Noust does not run. Turning it on proves the archive: the engine is made to switch segment and the
  archiver must report the new one; when it does not, the settings are put back.
- **Where things are.** The log is archived to `noust-wal/<system identifier>` in the `postgres`
  account's home (the engine writes there; Noust's backups directory is root's alone). The base
  backups are root's, in `<backups>/databases/pitr/postgresql/<system identifier>/`, each with a
  sidecar saying everything a recovery needs. A cluster that is replaced by another (a different
  system identifier) never mixes into the archive of the first.
- **The timer.** `noust-backup-pitr-postgresql.timer` runs `noust db pitr run` every five minutes: a
  base backup when the last is older than `--every` hours (weekly), retention, and sending the log and
  the base backups to the destinations (verified, with the same rules as every other upload; an
  encrypted destination is required under the ENS profile). One change at a time: the timer skips a
  run while a recovery or another change holds the lock.
- **Retention.** `--keep N` base backups and `--keep-days D` days back; with both, the one that keeps
  more wins, and the newest base backup always stays. "D days back" means any instant of the last D
  days can be recovered, so the base backup from before the window opens is kept too. The log that
  only a deleted base backup needed goes with it, here and on each destination (which can have its
  own limits: `--to nas:COUNT:DAYS`). Timeline histories are never pruned.
- **A recovery is in place and can be undone.** Before anything is touched it is planned: the base
  backup for the instant (the newest that finished before it), the log between them must be whole,
  the base backup must match the digest it was taken with, and an instant before the first base
  backup or after the archive is refused. Then the current segment is archived, the engine is stopped
  and *waited for until its server process is gone*, the data directory is **renamed aside, never
  deleted**, the base backup is extracted for the `postgres` account, the engine is started in
  recovery and waited for until it promotes. When it does not, the failed attempt is kept at
  `<data directory>.noust-failed-recovery`, the previous directory is put back and started, and the
  error carries the engine's own journal. Instants are UTC unless they carry an offset.
- **Recovering to the same instant twice, or to one before an earlier recovery.** Each recovery
  starts a new timeline. The engine's default follows the newest, which makes a second recovery to
  the same instant fail when nothing was written after the first ("recovery ended before configured
  recovery target was reached"), so Noust reads the timeline histories and follows a branch only
  when the instant is at or after the moment it split off.
- **What it does not do yet.** PostgreSQL of the server only (not an instance); a cluster with
  tablespaces besides the two every cluster has is refused; the archive directory is the engine's
  own and is not encrypted (the base backups and the destinations are subject to the encryption of
  [backups](disaster-recovery.md#backups-sealed-on-this-servers-disk) and of the destination); a recovery is not
  tried automatically in a scratch directory first (a restore drill of the archive).

The API is `GET /api/databases/pitr`, `POST .../pitr/enable/plan` and `.../pitr/restore/plan`, and, as
jobs with sudo mode, `POST .../pitr/enable`, `.../disable`, `.../base`, `.../sync`, `.../fetch` and
`.../restore`. A change that restarts the engine or replaces the cluster is refused without `confirm`.

## A host engine with a certificate clients can verify

A database made public is reached through a proxy that passes bytes, so the certificate the client
sees is the engine's own, and a package leaves a self-signed one: a client can encrypt but cannot know
who answers. `noust db tls` gives PostgreSQL, MySQL or MariaDB on the host a real certificate (an
ACME lineage, or one you uploaded) so `sslmode=verify-full` works from anywhere, with nothing
terminating TLS in between.

```bash
noust db tls enable -e postgresql --host db.example.com             # the lineage named db.example.com
noust db tls enable -e mysql --host db.example.com --source custom --certificate corp-db
noust db tls status
noust db tls refresh                       # what certbot runs after a renewal
noust db tls disable -e postgresql
```

- **Checked first.** The key is the certificate's, it covers the name, it has not expired. Then the
  certificate and key are copied to `/etc/ssl/noust-db/<engine>/` for the engine's account (the source
  stays root's: `/etc/letsencrypt/archive` is not a directory a database should be given).
- **Applied.** PostgreSQL by `ALTER SYSTEM` (`ssl`, `ssl_cert_file`, `ssl_key_file`, a TLS 1.2
  minimum) and a reload; MySQL and MariaDB by `98-noust-tls.cnf` and one restart (asked about).
- **Proved.** The engine is asked what it presents, with a TLS handshake started the way its protocol
  starts it and the name a client would send; the fingerprint must be the certificate's. When it is
  not, the settings are put back.
- **Renewed.** `noust db tls refresh` copies a changed source and makes the engine load it *without a
  restart* (`pg_reload_conf()`, `ALTER INSTANCE RELOAD TLS`, `FLUSH SSL`), then proves what is
  presented again. certbot's deploy hook (the one Noust installs for the web server) runs it after
  every renewal that succeeds. For an uploaded certificate it is the command to run after uploading a
  new one, and `status` says when the copy is behind its source.

The engine does not require TLS of every client because of this; what a public database's account
requires is in [Making a database public](#making-a-database-public-on-purpose). Redis, Valkey and
MongoDB are not covered, and an instance has its own certificate (`noust db instance tls`).

The API is `GET /api/databases/engines/tls` and `GET /api/databases/engines/{engine}/tls` (what an
engine is given, what it presents now, days left, whether its source was renewed),
`POST .../engines/{engine}/tls/plan` (checks the certificate and says whether the engine restarts) and,
as jobs with sudo mode, `PUT .../engines/{engine}/tls`, `DELETE .../engines/{engine}/tls` and
`POST /api/databases/engines/tls/refresh`. A restart is refused without `confirm`, as
`confirmation_required`, before the job exists. Tested against real PostgreSQL and MariaDB
(`host_tls`).

## Read replicas, over TLS

A replica is another server's database that follows this one's changes and answers read-only queries:
reports and backups can run on it, and it takes over (`promote`) if the primary is lost. It is the
engine's own replication (PostgreSQL's streaming replication; MySQL's and MariaDB's GTID replication)
driven by two commands, one on each server, and it is TLS-only: the primary needs a certificate
clients can verify (see the section above) and the replica connects with the certificate and the name
checked. This section is PostgreSQL's; [MySQL and MariaDB](#mysql-and-mariadb-replicas) follow.

```bash
# On the primary
noust db replica prepare reports --from 203.0.113.7 --host db.example.com --listen '*' \
    --open-firewall --token-file /root/reports.token --plan      # what it would do, and what stops it
noust db replica prepare reports --from 203.0.113.7 --host db.example.com --listen '*' \
    --open-firewall --token-file /root/reports.token             # asks before restarting PostgreSQL

# Carry the file to the replica server (it holds the replication password), then there
noust db replica join --token-file /root/reports.token --plan
noust db replica join --token-file /root/reports.token --replace-data

noust db replica status                    # on either server
noust db replica promote                   # on the replica: it becomes a primary of its own
noust db replica remove reports            # on the primary: the account, the slot and the line go
```

- **What `prepare` makes on the primary.** An account `noust_repl_<name>` with `REPLICATION LOGIN` and a
  generated password (SCRAM), a physical slot `noust_<name>` (so the primary keeps the log the replica
  has not read yet, from the moment the slot exists), and one marked line in `pg_hba.conf`:
  `hostssl replication noust_repl_<name> <address> scram-sha-256`. The line is checked by the server
  (`pg_hba_file_rules`) before it is loaded and put back as it was if the server finds an error in it;
  a connection without TLS finds no line, and the account cannot open any database. If PostgreSQL
  listens on the loopback only, `--listen` says where it should listen too (an address of this server,
  or `*`): that is the one thing that restarts it. `--open-firewall` allows the port from the
  replica's address through ufw or firewalld, and `remove` takes that rule away if Noust made it.
- **TLS is checked before anything is made.** The primary runs, against itself, the handshake the
  replica will run: the certificate must be signed by an authority the replica trusts and valid for
  `--host`. A private authority is given with `--ca-file` and travels in the join code. When the
  certificate would be refused, nothing is created and the message carries openssl's own words.
- **The join code** (`noust-replica:v1:...`) holds the primary's address, the account, its password,
  the slot, the authority and what the primary requires of a standby. It is a secret: it goes to a
  file only root reads (`--token-file`) or is printed once, and `noust db replica token <name>` prints
  it again on the primary. It is never part of a `--plan`, a log line, an audit event or the store.
- **What `join` does to the replica server.** The primary's cluster is copied with `pg_basebackup`
  into a directory beside this server's own, *while this server keeps running*: a primary that cannot
  be reached, whose certificate is not trusted, whose password is wrong or whose disk is too small to
  receive the copy changes nothing. Then PostgreSQL stops once (waited for until its server process is
  gone), the data directory is **renamed to `<data directory>.noust-before-replica`, never deleted**,
  the copy takes its place and PostgreSQL starts as a standby. The databases of this server are
  replaced by the primary's, which is why the plan lists them and `--replace-data` is asked for;
  `--discard-previous` deletes the old directory once the replica streams.
- **When the replica does not start following** (a setting the primary raised since, a version
  mismatch, a primary that went away) the failed attempt is kept at
  `<data directory>.noust-failed-replica`, the previous directory is put back and started, and the
  error carries the engine's journal. A replica is a physical copy, so the major version of
  PostgreSQL must be the same on both servers.
- **What the replica keeps of itself.** The copy brings the primary's `postgresql.auto.conf` (every
  `ALTER SYSTEM` the primary had: its certificate paths, its archiving, its addresses) and, on layouts
  that keep configuration in the data directory, its configuration files. A replica is another
  machine: it keeps its own settings and files, takes the capacity settings a standby must match
  (`max_connections`, `max_worker_processes`, `max_wal_senders`, `max_prepared_transactions`,
  `max_locks_per_transaction`: the larger of its own and the primary's), and follows. The password is in
  a passfile of the `postgres` account (`~postgres/.noust-replica/<name>.pgpass`, 0600) that
  `primary_conninfo` names, never in a setting.
- **Watching it.** `status` on the primary reads each replica from the engine itself: whether it is
  connected, `streaming` or catching up, how far behind in bytes, the TLS version of its connection
  (`pg_stat_ssl`: a connection without TLS is called out), and how much log its slot holds. A replica
  that is gone while its slot stays makes the primary keep the log until its disk fills, so `status`
  warns when a slot holds more than 2 GiB of a replica that is not connected. On the replica it shows
  the receiver, the replay delay and the `sslmode` it follows with.
- **Promote and remove.** `promote` ends the following (`pg_promote`), forgets where it followed and
  deletes the passfile; the old primary keeps the slot until `noust db replica remove <name>` is run
  there, and the message says so. `remove` takes away the account, the slot (ending the sender first),
  the `pg_hba.conf` line, the firewall rule Noust made and the password; `--restore-listen` also
  puts `listen_addresses` back (a restart) when no other replica needs it.
- **What it does not do yet.** PostgreSQL of the server only, not an instance; a replica of a replica
  (cascading) and synchronous replication are not set up; point-in-time recovery must be off on a
  server that joins (the copy has the primary's identity) and is turned on again after promoting; no
  automatic failover: promoting is a decision; the console does not show replicas yet (the
  commands are the interface).

Tested against two real servers on one network (`tests/integration/engines_run.py`, scenario
`replication_postgresql`): the engine refuses a connection without TLS and a wrong password, the
replica copies the cluster, streams, refuses writes and is seen on TLS 1.3 by the primary's
`pg_stat_ssl`; a join with a wrong password or an untrusted certificate changes nothing; a standby
that cannot start puts the previous data directory back; promoting and removing leave both servers
as independent as before.

### MySQL and MariaDB replicas

The same two commands with `--engine mysql` (it covers MariaDB too, whichever is installed); `join`
reads the engine from the code.

```bash
# On the primary
noust db replica prepare reports -e mysql --from 203.0.113.7 --host db.example.com \
    --listen '*' --open-firewall --token-file /root/reports.token --plan
noust db replica prepare reports -e mysql --from 203.0.113.7 --host db.example.com \
    --listen '*' --open-firewall --token-file /root/reports.token

# On the replica (the same flavour, the same version or newer)
noust db replica join --token-file /root/reports.token --plan
noust db replica join --token-file /root/reports.token --replace-data

noust db replica status -e mysql
noust db replica promote -e mysql
noust db replica remove reports -e mysql --restore-listen
```

- **What `prepare` gives the primary.** A file of Noust's, `99-noust-replication.cnf`, in the include
  directory (it sorts after the package's files, so its settings win, and removing it puts them back):
  binary logging with a log expiry where MariaDB had none, GTIDs on where MySQL had them off
  (`gtid_mode`, `enforce_gtid_consistency`), and the address to listen on when `--listen` says so.
  Anything of those the server already has is not written, and a server that needs none of it is not
  restarted. MariaDB listens on one address, so it takes `--listen '*'` (written as `0.0.0.0`); MySQL
  takes a list. Binary logging turned off on purpose in MySQL (`skip-log-bin`) is refused with the fix,
  not overridden.
- **The account.** `noust_repl_<name>` at the replica's address (a network becomes `address/netmask`),
  `REQUIRE SSL`, a generated password of 32 characters (the most MySQL takes for a replication user),
  granted `REPLICATION SLAVE`, `REPLICATION CLIENT`, `RELOAD`, `SELECT`, `SHOW VIEW`, `TRIGGER`,
  `EVENT` and `LOCK TABLES`. **That account can read**, which PostgreSQL's cannot: the first copy of a
  logical replica is a dump taken through it. It cannot write, it connects from one address, and the
  engine itself refuses it without TLS (MariaDB: `Access denied`; MySQL 8: `Authentication requires
  secure connection`). `remove` drops it.
- **TLS** is proved before anything is made, with the handshake of the MySQL protocol and the name the
  replica will use, and verified by the replica both ways: the dump (`--ssl-verify-server-cert`,
  `--ssl-mode=VERIFY_IDENTITY`) and the replication itself (`MASTER_SSL_VERIFY_SERVER_CERT = 1`,
  `SOURCE_SSL_VERIFY_SERVER_CERT = 1`). The authority is kept in
  `/etc/ssl/noust-db/mysql-replica/<name>/ca.pem` for the server's account. The password is in the
  environment of the two clients that talk to the primary and, as it must be, in the server's own
  replication metadata; never in an argument.
- **What `join` does.** In this order, and each step before the first change costs nothing: the
  primary is asked for its databases over TLS (a primary that cannot be reached, trusted or signed in
  to stops here, with the client's words); each user database of this server is dumped with
  `noust db backup` (they are replaced, and these dumps are how they come back); the primary's databases
  are dumped at one GTID position (`mysqldump --single-transaction --routines --triggers --events`,
  `--master-data=1 --gtid` on MariaDB, `--set-gtid-purged=ON` on MySQL) into a private file; this
  server gets its own `server_id` (stable for the name, never the primary's) in `99-noust-replica.cnf`,
  and MySQL a restart for GTIDs; its databases are dropped and the dump loaded; the server is pointed at
  the primary and started; and only when both threads run is it made read-only.
- **When any of that fails after the first change** (a load the server refuses, a primary that goes
  away, a replication that stops with an error) replication is stopped and forgotten, what was loaded is
  dropped, the dumps made first are loaded back, Noust's settings are taken off and the server restarted
  if it was, and the error carries the server's own words. The primary's dump is deleted on every way
  out; `--discard-previous` also deletes the dumps of this server's own databases once it streams.
- **What is not copied.** The accounts of the primary (they live in the `mysql` schema, which is not
  part of a copy of the user databases); the ones created on the primary after the join replicate like
  any statement. And MariaDB's `read_only` does not stop the administrator, as it never has: ordinary
  accounts are refused, `root` is not (MySQL's `super_read_only` refuses everyone).
- **Promote and remove.** `promote` stops reading from the primary, waits for what it already read to
  be applied, stops and forgets the following and lifts read-only. `remove` drops the account (every
  host it exists for), the firewall rule Noust made and the password; `--restore-listen` takes
  `99-noust-replication.cnf` off and restarts the server.
- **What it does not do yet.** MySQL older than 8.0.23 and MariaDB older than 10.4 are refused (they
  lack GTIDs with verified TLS, or `CHANGE REPLICATION SOURCE`); one flavour per pair (a MariaDB
  replica of a MySQL primary is not offered); a replica of a replica; multi-source or semi-synchronous
  replication; no automatic failover.

Tested against two real servers on one network (`tests/integration/engines_run.py`, scenarios
`replication_mariadb` with MariaDB 10.11 and `replication_mysql` with MySQL 8.4 from Oracle's
repository): the engine refuses the account without TLS, with a wrong password, and for anything but
reading; a join with a wrong password or an untrusted certificate changes nothing; a load the server
refuses (`max_allowed_packet`) puts the databases back, restarting MySQL twice; the replica follows
over `Master_SSL_Verify_Server_Cert: Yes`, holds the routines, refuses an ordinary account's write,
receives new accounts and rows, and after `promote` and `remove` both servers are independent again.

The API for all of this is `GET /api/databases/replicas` and `GET .../replicas/{engine}` (`postgresql`
or `mysql`), `POST .../replicas/{engine}/plan`, and, with sudo mode, `POST .../replicas/{engine}` (make
room for a replica; it answers with the join code, which holds the replication password and is
therefore never a job's result), `POST .../replicas/{engine}/{name}/token` (show it again; audited as
`db.replica.token`), `POST .../replicas/join/plan` and `POST .../replicas/join` (a job: the data of the
replica is replaced by a copy of the primary's), `POST .../replicas/{engine}/promote` and
`DELETE .../replicas/{engine}/{name}`. A restart, a replaced data directory and a promotion are refused
without `confirm` (`replace_data` for the join) as `confirmation_required`, before anything is done.
They are the server's: a team member does not reach them. The console does not show replicas yet.

## Making a database public, on purpose

A database is private: its engine listens on the loopback and `noust db connect-info` prints the SSH
tunnel to reach it. For a desktop client on your own computer there is a deliberate, reversible action:

```bash
noust db public open shop -e postgresql --from 203.0.113.7 --access read-write   # asks first
noust db public list
noust db public show shop -e postgresql --reveal      # the connection, with the password
noust db public close shop -e postgresql              # undoes all of it
```

Opening does four things, in this order, each recorded so it can be undone, and undoes the earlier
ones when a later one fails:

1. **A proxy on the public port you choose** (`--port`, 1024 to 65535; a free one next to the engine's
   is suggested), on the address you choose (`--bind`, every IPv4 address by default): a systemd
   socket and a service running `systemd-socket-proxyd` (`noust-dbexpose-<engine>-<port>`), forwarding
   to the engine's loopback port. The engine is not reconfigured and not restarted, nothing already
   connected notices, and it keeps listening on the loopback only.
2. **An account of its own** for that database, `<database>_remote`, with the access you chose
   (`--access owner|read-write|read-only`, owner by default) on that database alone. Applications'
   accounts and passwords are never the ones given out. The password is shown once and kept as a
   secret (`show --reveal`, sudo mode, shows it again).
3. **TLS required of that account where the engine can**: PostgreSQL refuses it without (one marked
   `hostnossl ... reject` line above every other in `pg_hba.conf`, checked by PostgreSQL before it is
   reloaded and put back if it refuses it), MySQL and MariaDB refuse it without (`REQUIRE SSL` on
   the account). Redis and MongoDB cannot do that for one account, so for them TLS is optional only
   when you say so (`--allow-plaintext`): the password and the data then cross the network in clear.
4. **The firewall**, through the one firewall manager (ufw or firewalld): an allow rule for the port from
   each `--from` address or network, or from anyone when none is given. With no firewall that Noust
   changes, or an inactive one, the warnings say that nothing limits who reaches the port.

Everything it will do, and what you accept by going ahead (anyone who can reach the port can try the
account's password; a Redis account reaches every key), is printed first and asked about (`--yes`;
`confirm` in the API). It is refused whatever you confirm under the `ens-medium` profile, for an engine in a
container Noust did not make (its compose file publishes its ports), for Redis without a password and
MongoDB without authorization (a public port in front of them would be an open door), and for a port that
is taken.

An **instance Noust runs** is public the same way, because Noust publishes it on 127.0.0.1 itself: the
proxy forwards to that port (`noust-dbexpose-postgresql.pg16-<port>`, ordered after the instance's
unit, as the host engine's is) and the instance stays private. What differs is TLS: no
account inside an instance can be made to require it, so PostgreSQL and the other engines whose image
has TLS off are opened only with `--allow-plaintext`, and the plan says so first.

`close` removes what the record says was made and nothing else: the firewall rule only if Noust added it
(an identical rule of yours is left), the units, the TLS line and the account. One step that cannot be
done now (the engine is stopped) leaves the record `closing` and is named; running `close` again
finishes it. The API is `GET /api/databases/public`, `POST .../public/plan`, `POST .../public` (sudo mode),
`GET .../public/{engine}/{database}`, `POST .../reveal` (sudo mode) and `DELETE .../public/{engine}/{database}`
(sudo mode).

## Exposure

`noust db exposure` lists database ports reachable from the network. A port Docker publishes that
the `DOCKER-USER` chain refuses on the public interface is reported as closed by the firewall, not
as open.
