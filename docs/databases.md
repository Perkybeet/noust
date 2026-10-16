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

## Installing an engine

```bash
noust db catalog                                  # what this server can install, and why not
noust db install postgresql --version 17          # from the PostgreSQL project's repository
noust db install mariadb --version 11.4
noust db install valkey
```

Upstream repositories are added with their signing keys pinned by fingerprint. MySQL and MariaDB,
and Redis and Valkey, are never installed beside each other.

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
| MongoDB | 8.0, 7.0 |

`--version` matches by components (`8` is MySQL 8.4; `11` is ambiguous for MariaDB and says so).
A new version is added to the catalog by a release of Noust, after reading it from its registry.

**KeyDB** is a fork of Redis 6.2 that speaks its protocol and keeps its files, so it is driven by the same
code as Redis and Valkey (keys browser, accounts, profiles, snapshots, metrics, backup policies). It only runs as
an instance: no distribution packages it, so `noust db install keydb` says to make one
(`noust db instance create cache --engine keydb`). Its project has published nothing since 6.3.4 (October 2023),
and publishes no end-of-support date; Noust pins the image the project itself publishes under the tag `latest`
(both processors) by digest, like the rest. Dragonfly is not offered: it saves snapshots in its own format
(`.dfs`, several files) and the ACL rules it documents have no hashed passwords, which are what Noust's
snapshot dumps and read-only accounts rely on. It will be offered when both can be done without guessing.

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
the host's engines, and says so for an instance); ClickHouse is not offered (it needs its own engine
manager). An instance can serve TLS ([below](#tls-for-an-instance-and-for-a-link)); Redis and Valkey
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
  the rest; MariaDB can refuse plaintext from 11.4. Redis and Valkey cannot serve TLS yet. A mode the
  engine cannot serve is refused, naming why, before anything changes.
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
