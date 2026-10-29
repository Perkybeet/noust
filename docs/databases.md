# Databases

Noust manages the databases of the server it runs on: the engines installed on the host, the
database containers Docker runs (from 3.3), and, from 4.0, the **instances** it runs itself: another
version of an engine, or one the host does not provide, in a container of its own. The console's
Databases area and `noust db` are two faces of the same code.

The rest of this guide is on pages of its own:

- [Install an engine, in the version you choose](databases-engines.md)
- [Instances: another version, or an engine the host lacks](databases-instances.md)
- [Extensions, and dumps from elsewhere](databases-import.md)
- [Move a database, upgrade an engine, or go back to an instant](databases-move.md)
- [TLS and read replicas](databases-tls.md)
- [Settings, limits and exposure](databases-settings.md)

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
noust db tables shop --engine postgresql@shop.postgres
noust db backup shop --engine postgresql@shop.postgres
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

A script gives the password on standard input, one line, without it reaching a command line or a
warning on standard error (every secret a command asks for is read that way):

```bash
printf '%s\n' "$ROOT_PASSWORD" | noust db config --engine mysql --user root --password
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
