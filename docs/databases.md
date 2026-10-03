# Databases

Noust manages the databases of the server it runs on: the engines installed on the host and,
from 3.3, the database containers Docker runs. The console's Databases area and `noust db` are
two faces of the same code.

## Where a database lives

| Key | What it is |
|---|---|
| `postgresql`, `mysql`, `redis`, `mongodb` | the engine installed on the host |
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
configure an engine inside a container: that is the image's and the compose file's.

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

## Exposure

`noust db exposure` lists database ports reachable from the network. A port Docker publishes that
the `DOCKER-USER` chain refuses on the public interface is reported as closed by the firewall, not
as open.
