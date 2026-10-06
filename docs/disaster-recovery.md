# Backups and disaster recovery

Noust backs up three different things, each with its own command, schedule and retention.
Together they are enough to bring a server back from nothing.

| What | Command | Timer | Restores with |
|------|---------|-------|---------------|
| An application's code and configuration | `noust backup` | `noust-backup-<app>` | `noust backup restore` |
| The databases | `noust db backup`, `noust db backup-schedule`, `noust db backup-run` | `noust-backup-db-<engine>-<db>` | `noust db restore`, `noust db import` |
| An application's data (volumes, persistent paths) | `noust backup volumes` | `noust-backup-vol-<app>` | `noust backup volumes restore` |
| Noust's own state | `noust self-backup` | `noust-backup-self` | `noust self-backup restore`, `noust restore-server` |

Every destination (rclone) is the same one for the four of them: configure it once with
`noust backup destination`.

## Databases

PostgreSQL, MySQL, MariaDB and MongoDB are covered; a SQLite file inside an application is
protected by the application's own backup, which takes it with SQLite's online backup so it
is consistent while the application writes.

```bash
noust db backup-schedule set app_db -e postgresql --schedule daily --keep 7
noust db backup-schedule set --all -e postgresql --schedule daily       # every database on the engine
noust db backup-schedule set --all -e postgresql --exclude-database scratch
noust db backup-schedule set app_db -e postgresql --exclude-schema audit --exclude-table logs
noust db backup-schedule set app_db -e mongodb --exclude-table sessions   # a MongoDB collection
noust db backup-run app_db -e postgresql                                 # now, the way the timer does it
```

An all-databases policy skips the databases that have a policy of their own and the ones it
excludes. When one database fails the others are still dumped, and the run reports the failure
at the end. ClickHouse is not covered: Noust has no ClickHouse engine manager yet.

### Importing a dump

```bash
noust db import app_db /srv/dumps/app.sql.gz -e postgresql --inspect   # what is it, can the engine load it
noust db import app_db /srv/dumps/app.sql.gz -e postgresql             # load it
noust db import app_db /srv/dumps/app.sql.gz -e postgresql --replace   # drop the database first
```

The format is read from the file's bytes (SQL, custom and tar PostgreSQL dumps, MongoDB
archives, gzip, bzip2, xz, zip), never from its name. A server path must be under
`<backup directory>/imports` or a directory listed in `databases.import_directories`. The
database is dumped first and put back if the load fails; a database the import created is
dropped again. PostgreSQL owners and privileges in the dump are dropped unless `--keep-owners`
says otherwise, because they are the roles of the server it came from.

The console uploads a file from the browser in chunks (`/api/databases/imports/uploads`),
resumable at the offset the server reports, up to `databases.import_max_bytes` (10 GiB by
default). Upload files are private to Noust (mode 0600) and swept after 24 hours.

## Application data

The persistent paths of an application on releases, the named volumes of a Compose stack and a
recipe's data directories are backed up as one archive each:

```bash
noust backup volumes targets shop.example.com
noust backup volumes schedule set shop.example.com --schedule daily --keep 7 --to nas:14 --consistency pause
noust backup volumes run shop.example.com        # now, the way the timer does it
noust backup volumes verify shop.example.com <id> --deep
noust backup volumes restore shop.example.com <id>
```

`--consistency` is `live` (copy while it runs; SQLite files inside are snapshotted), `pause`
(freeze the application for the copy) or `stop` (stop it, copy, start it again). An application
stopped for a backup is always started again, also when the copy fails. A restore takes a
safety copy of what is there, replaces the data (it does not merge), starts the application
through the health gate and puts the old data back when the gate fails.

## Noust's own state

```bash
noust self-backup create                         # asks for a passphrase
noust self-backup list
noust self-backup verify noust-self-<host>-<time>.tar.gz.enc
noust self-backup schedule set --schedule daily --keep 7 --to nas:30
noust self-backup restore noust-self-<host>-<time>.tar.gz.enc
```

A bundle is one file, sealed with a passphrase (scrypt, then AES-256-CBC and HMAC-SHA256
through `openssl`, the same envelope as a central's sealed secrets). It holds the store
(taken with SQLite's online backup, so it is consistent), the configuration, the secrets and
keys (fleet, SSH, sealing), the certificates, and the unit files and drop-ins Noust generated.
It does not hold the metrics and observation databases (they regenerate), application backups
or the sites' web server files (they are rendered again from the store).

The passphrase is never an argument. Give it at the prompt, in a file only its owner can read
(`--passphrase-file`), in `NOUST_SELF_BACKUP_PASSPHRASE` or as one line of standard input.
**Keep it outside this server**: without it the bundle cannot be read, and it is the only thing
the bundle does not carry. A scheduled backup needs the passphrase unattended, so `schedule
set` stores it in Noust's secret store on this server, which a bundle sent elsewhere does not
share a disk with.

`verify` proves a bundle restorable without restoring it: the envelope, every file's digest,
the store's integrity check and that this Noust can migrate its schema. `restore` first checks
every path in the bundle against this server's own directories (a bundle never decides where a
file goes), refuses to run over a server that already holds state without `--force` (and then
takes a bundle of what was there first), stops Noust's services, puts the files back, runs the
store's migrations and starts everything again.

## Bringing a whole server back

On a new machine with Noust installed and the bundle on it (or on a destination):

```bash
noust restore-server --from noust-self-<host>-<time>.tar.gz.enc --plan     # what it would bring back
noust restore-server --from noust-self-<host>-<time>.tar.gz.enc
noust restore-server --from-destination nas --name noust-self-<host>-<time>.tar.gz.enc --host <old-host>
```

The flow restores Noust's state, names the database engines this machine lacks (with the
`noust db install` command for each), then for every application restores the newest backup
(local, or on the destinations its schedule sends to), lays the newer data backups over it,
renders its site and starts it through the health gate. The final report says per application
what came back, what failed (with the evidence) and what had no backup. It exits 1 when any
application is not back; fix what it says and run it again with `--skip-state` to retry only
the applications.

What it does not do: install the web server, Docker or the database engines (it says which are
missing), and deploy an application that never had a backup (it says so; deploy it again from
its source). A schema change that nothing can undo the database for needs
`--accept-schema-changes`, as every other way back does.

## Configuration

| Key | Default | |
|-----|---------|---|
| `databases.import_max_bytes` | 10 GiB | Largest dump the console accepts |
| `databases.import_directories` | none | Server directories `noust db import` may read from |

Permissions: the database import routes need `databases.manage`; volume and self backups use the
`backups.*` permissions, and changing the schedule of Noust's own backup is root-equivalent (sudo
mode). Everything is in the audit trail: `db.import`, `db.import.upload`, `volume_backup.*`,
`self_backup.*` and `restore_server.run`.
