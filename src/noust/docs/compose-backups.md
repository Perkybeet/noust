# Stack database backups and restore

Part of [Docker Compose](compose.md).

Going back to the previous containers puts the previous code back, never the data: a migration
the failed attempt ran stays in the database. Before 3.2 the copy an update took held, at best, a
tarball of a live database's data directory, which nobody can restore. Now it holds a dump.

**What is detected.** The services whose image is the official `postgres`, `mysql`, `mariadb`
or `mongo` (any tag, `-alpine` included, from Docker Hub). The user and database come from the
service's environment as Compose resolves it (`docker compose config --format json`), so a
`${DB_USER}` filled from `.env` is what the container really has: `POSTGRES_USER` and
`POSTGRES_DB`, `MYSQL_*` or `MARIADB_*`, `MONGO_INITDB_*`. Other images (`bitnami/postgresql`,
`postgis/postgis`) are not detected; declare them:

```yaml
# noust.yaml
backup:
  databases:
    - service: db
      engine: postgres        # postgres | mysql | mariadb | mongo
      database: app           # optional: read from the service's environment
      user: app               # optional: the same
```

A list replaces detection. A user or database name has to be plain (letters, digits and `_ . @
$ -`, starting with a letter or digit); for a database that is not one of the four engines, back
it up with a `pre_deploy` hook of your own.

**How it is dumped.** Through the engine's own client, inside the service's container, with
`docker compose exec -T`:

| Engine | Dump | Restore |
|---|---|---|
| Postgres | `pg_dump -Fc` | `pg_restore --single-transaction --clean --if-exists` |
| MySQL | `mysqldump --single-transaction --no-tablespaces` | `mysql` |
| MariaDB | `mariadb-dump` (or `mysqldump`) `--single-transaction` | `mariadb` (or `mysql`) |
| MongoDB | `mongodump --archive` | `mongorestore --archive --drop` |

The password is read inside the container, from its own environment (`POSTGRES_PASSWORD`,
`MYSQL_ROOT_PASSWORD`, `MONGO_INITDB_ROOT_PASSWORD`...) or from the file a `*_FILE` variable
names, and handed to the client the way it takes a secret outside a command line. It is in no
argument on the host or in the container. The dump goes straight to a compressed file, mode
`0600`, named `stack-<service>-<engine>-<database>.<dump|sql|archive>.gz`, under
`noust-backup/databases/` in the backup's archive, with a manifest entry that says how to put it
back.

**When.** In the backup every update of a Compose stack takes first, before the `pre_deploy`
hooks run and before anything is recreated. It is on by default for a stack that has databases.
`noust backup create DOMAIN --include-databases` includes them in a backup of your own, and that
is not switched off by `backup.databases: off`, which is about the automatic copy.

**If the copy fails, the update stops.** Without it there is no way back from a migration. The
error has the engine's own output and the command that carries on without it:

```
To update shop.example.com without a copy of its databases, run: noust app backup-before-update
shop.example.com off. To keep the copy, fix what the output above says and run the update again.
```

**Turning it off**, when the database is backed up another way or cannot be dumped from its
container:

```bash
noust app backup-before-update shop.example.com          # shows it
noust app backup-before-update shop.example.com off      # or on
```

or `backup.databases: off` in `noust.yaml`, or `PATCH /api/apps/{domain}/backup-before-update`
with `{"enabled": false}` (`apps.manage`, sudo mode), or the application's Deploys and triggers (Automation) in
the console. `GET /api/apps/{domain}` shows `backup_before_update`.

**Restoring.** With the stack's service reachable through its compose file:

```bash
noust backup list shop.example.com
noust backup restore BACKUP_ID --databases-only     # put back only the databases; every file stays
noust backup restore BACKUP_ID                      # files and databases, as the backup was taken
```

The application is stopped while the dumps go back and started again if it was running. A
database's container is started alone when it is not running and stopped again afterwards. The
restore replaces what the database holds (`--clean --if-exists`, `--drop`), and Postgres does it
in one transaction, so a dump it cannot read whole leaves the database as it was. Do this after
going back past a migration, as [Rolling back past a migration](compose-hooks.md#rolling-back-past-a-migration)
says.

**Volumes.** `noust backup create --include-docker-volumes` now copies the volumes Docker
really has: `<project>_<key>` unless the volume names itself with `name:` (or is `external`),
read from `docker compose config`. Before, it mounted the key, which is a new, empty volume.

**Every night, and tried.** A recipe installed on Compose keeps its database in the stack, so the
daily policy that protects the databases in Noust's store never reached it. The recipe's default
backups now make the application's own schedule, which dumps each of those databases through its
engine's client, and the [restore drill](disaster-recovery.md#does-a-backup-come-back) loads every dump
into a throwaway container of the same image the service ran (the manifest records the image) and
counts what it holds. The container has no network, a name and a label of its own, and is removed by
name; a dump that does not load fails the drill with the engine's own words.
