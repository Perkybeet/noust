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
| MongoDB | 8.0, 7.0 |

`--version` matches by components (`8` is MySQL 8.4; `11` is ambiguous for MariaDB and says so).
A new version is added to the catalog by a release of Noust, after reading it from its registry.

**Upgrading within a version.** `noust db instance upgrade pg16` asks the registry what image the tag
`16` is *now* and moves the instance to it, keeping the digest it ran. Before touching anything it
dumps every database (a dump that fails stops the upgrade; `--no-backup` skips it), then restarts the
instance on the new image and waits for it to be healthy and to accept its superuser. When it does not,
the previous image is put back and the error carries the container's own log, secrets masked. It does
not change the major version: PostgreSQL 16 stays 16. A new major version is another instance: make it,
dump each database from the old one (`noust db backup`) and load it into the new one
(`noust db restore --engine postgresql@pg17`), create the application's account there and link it
(`noust db provision --instance`, `noust db link`), and keep the old instance stopped for as long as you
want a way back. A guided move is not part of this release.

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
the host's engines, and says so for an instance); TLS cannot be required of an account inside an
instance, so a database made public on one is plaintext unless you say so (`--allow-plaintext`) or
the server already has TLS (MySQL's image makes its own certificate); there is no guided move to a new
major version, and ClickHouse is not offered (it needs its own engine manager).

The API is under `/api/databases/instances`: `GET` the list, `GET .../catalog`, `POST .../plan` (what a
creation would do, changing nothing), `POST` to make one (sudo mode, a job), `GET .../{name}`,
`POST .../{name}/start` (a job), `POST .../{name}/stop`, `POST .../{name}/upgrade` (sudo mode, a job),
`GET .../{name}/remove-plan` and `DELETE .../{name}?confirm=true` (sudo mode, a job). They need
`databases.read`, `databases.manage` (and `apps.operate` for start and stop), and are the server's: a
team member does not see them, but picks one for its application's database through
`GET /api/databases/provisioning/choices`.

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
