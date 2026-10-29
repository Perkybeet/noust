# Move a database, upgrade an engine, or go back to an instant

Part of [Databases](databases.md).

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
output and revealed in the console with sudo mode, or rotated with `noust db user-password`), loads the dump, gives each account its
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
