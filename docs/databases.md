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

## A database on the engine and version you choose

```bash
noust db choices                                  # what a database can be created on, and how
noust db provision shop.example.com -e postgresql --version 17 --install
noust create -d shop.example.com -s github:me/shop --database postgresql:shop --database-version 17
```

`--version` is numbers and dots (`17`, `11.4`) and matches by components: `17` is any 17.x, and
`11` is not 10.11. Without it nothing changes: the database is created on the engine that is
installed. Noust runs one instance of each engine on a host, so:

- the version that runs is used;
- another version than the one that runs is refused with what runs (`PostgreSQL 17 was asked for,
  but this server runs PostgreSQL 16.4`), before anything is created. Several versions side by
  side are the container engines of 3.4;
- MariaDB is not MySQL and Valkey is not Redis: asking for the one that is not installed is refused
  the same way;
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
container (its compose file publishes its ports), for Redis without a password and MongoDB without
authorization (a public port in front of them would be an open door), and for a port that is taken.

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
