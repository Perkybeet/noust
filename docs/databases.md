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
service. Recording one (the console's "Record the link", or `noust db adopt` for every
unambiguous one) makes it a link without touching the application.

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
tool, applied, and put back if the engine does not come back. Listening beyond the loopback asks
for confirmation (`--yes`) and is refused under the `ens-medium` profile.

## Exposure

`noust db exposure` lists database ports reachable from the network. A port Docker publishes that
the `DOCKER-USER` chain refuses on the public interface is reported as closed by the firewall, not
as open.
