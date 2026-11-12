# Bring a whole server back, or move to another

Part of [Backups and disaster recovery](disaster-recovery.md).

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

With `--install` it also installs what the machine lacks: the web server the restored configuration
names, Docker when an application or a database instance needs it, and the database engines (apt, dnf
on Fedora and zypper, from the distribution's own repositories; a distribution that does not carry Docker,
or whose Compose is not v2 (Debian 12 and 11 package Compose 1), is told how, never given a
third-party repository). Without it nothing is installed and the report names the
command. Once an engine is there the flow creates the PostgreSQL roles the new server lacks, then loads
each database no application owns from its newest dump, on this server or on a destination, and never
over a database the server already has. The report lists the software, the roles and the databases, and a
database with no dump anywhere makes it not ok.

The console runs everything after the state is back: `GET /api/server/restore/plan` says what a
restore would do now, and `POST /api/server/restore` (a job, sudo mode, `backups.manage`) does it.
Restoring the state replaces the store the console runs on, so that step stays a command at a terminal
and the job keeps the state that is here.

What it does not do: deploy an application that never had a backup (it says so; deploy it again from
its source). A schema change that nothing can undo the database for needs `--accept-schema-changes`,
as every other way back does.

## Moving to another server

Bringing a server back and moving to another one are the same restore with two steps around it, and
`noust server migrate` is those two steps. Nothing in them stops, changes or removes anything on the old
server (it keeps answering until DNS points at the new one), and none of it edits DNS.

```bash
# On the old server
noust server migrate plan --to nas        # what would move, what the new server needs, what stops it
noust server migrate prepare --to nas     # copy everything now and send it to the destination

# On the new server (Noust, the web server and Docker installed; the engines the plan named)
noust restore-server --from-destination nas --name noust-self-<host>-<time>.tar.gz.enc --host <old-host>
noust server migrate check                # does everything answer? where do the names resolve now?
```

**`prepare`** takes a fresh copy of everything, in an order that makes the bundle consistent with it, and
sends each piece to a destination both servers can reach: every application (its release, `.env`, the dumps of
its databases and its Docker volumes, as `noust backup create` takes it), the data of every application
(persistent paths and volumes, like `noust backup volumes`), the databases no application owns, and last the
bundle of Noust's own state, sealed under a passphrase that is never sent (`--passphrase-file`,
`NOUST_SELF_BACKUP_PASSPHRASE`, a prompt, or `--stored-passphrase` for the one the self-backup schedule keeps).
Each piece is its own step: one that fails (a volume that is gone, a destination that refused one file) is
reported and the others still go, so a single bad piece does not leave twenty applications without a copy.
It prints the commands to run on the new server, with the bundle's name and this server's folder filled in.
`--app` limits it to some applications, `--no-data` leaves the data out. It is safe to repeat: for a final
copy, stop what writes to the applications and run it again.

**`plan`** is the same without copying: each application with every name it answers on, its databases, how
many pieces of data it has and when it was last backed up; the standalone databases; the database engines the
new server needs (`noust db install`); and what stops it, which is a destination that does not exist or that
cannot be reached.

**`check`** runs on the new server after `restore-server` and asks every application what a person asks
before moving DNS: does its unit (or its stack) run, does it answer the way a deploy's health gate asks
(the probe's own words when it does not), is a certificate there for each of its names, are its databases up,
and where does each name resolve now. An application that is down, or whose database is missing, makes the
report fail (exit status 1); a name that still resolves to the old server is listed as *pending*, not as wrong,
with the addresses it resolves to: pointing it at the new server is yours to do, and `noust cert create` issues
the certificates of the names that had none once DNS answers. The report is dated and is also
`GET /api/server/migration/check`.

What it does not do: install the web server, Docker or the engines on the new server (the plan and
`restore-server` name what is missing); move the containers of a database *instance* (their databases come in
the applications' backups, the instance itself is made again with `noust db instance create`); or touch
anything that is not under Noust's care. The API is `GET /api/server/migration/plan`,
`POST /api/server/migration/prepare` (sudo mode, a job, `backups.manage`) and `GET .../check`; the restore stays
a command at a terminal, because it replaces the store the console runs on.
