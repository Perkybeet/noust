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
is consistent while the application writes. `noust db sqlite list` shows which files an
application keeps, `check` asks SQLite whether one is sound, and `copy` takes a consistent copy
of one before a risky change (see [databases](databases.md#sqlite-files)).

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

A PostgreSQL dump from outside is loaded as the database's owner, not as the server's superuser, so a
dump that runs `COPY ... FROM PROGRAM` is refused by the server itself; `--as-superuser` is the explicit
way for a dump you trust, and `noust db import-all` brings a whole `pg_dumpall`
([details](databases.md#a-dump-from-elsewhere-and-a-whole-servers-databases)).

The format is read from the file's bytes (SQL, custom and tar PostgreSQL dumps, MongoDB
archives, gzip, bzip2, xz, zip), never from its name. A server path must be under
`<backup directory>/imports` or a directory listed in `databases.import_directories`. The
database is dumped first and put back if the load fails; a database the import created is
dropped again. PostgreSQL owners and privileges in the dump are dropped unless `--keep-owners`
says otherwise, because they are the roles of the server it came from.

The console uploads a file from the browser in chunks (`/api/databases/imports/uploads`),
resumable at the offset the server reports, up to `databases.import_max_bytes` (10 GiB by
default). Upload files are private to Noust (mode 0600) and swept after 24 hours.

### Dumps that are really dumps

A dump that exits cleanly and wrote nothing (a client killed mid-pipeline, a connection to the wrong
place) is refused and its file removed, not left in the backup directory with a valid name: an empty
file, or a gzip of nothing, fails where every engine's dump passes. A dump that fails or runs past its
deadline leaves no file, and `dump | gzip` does not hide a failing producer.

### Keeping by size

A policy keeps by number and age, and by size too: `noust db backup-schedule set app_db -e postgresql
--keep 14 --keep-days 30 --keep-gb 20`, and `--to nas:14:30:50` for a destination (name, count, days,
gigabytes). The newest dumps that fit are kept, counting from the newest back; one that does not fit
ends the list (a window of time, not a pile of whatever is small); and the newest always stays,
whatever its size, so a limit never leaves a database with no dump. Dumps taken by hand and the safety
copy of a restore are never retention's. Application, data and Noust-state backups keep by number and
age only.

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
stopped for a backup is always started again, also when the copy fails. A SQLite database an application is writing is taken with SQLite's own backup API and put in the
archive under the file's name, with its `-wal`, `-shm` and `-journal` files left out, so a copy never
holds a transaction half-way; a file that only looks like a database is archived as it was. An
application stopped for a copy is started again also when the process is told to stop (SIGTERM, what
systemd sends to a timer's unit), and if it still cannot be, the error says which unit and the command
to start it. A Docker volume archived while its application runs (`--consistency live`) that turns out
to hold an engine's data directory (PostgreSQL's `PG_VERSION`, InnoDB's `ibdata1`, MongoDB's
`WiredTiger.wt`, Redis's `appendonlydir`) is flagged in the log and the audit record as a risk, with the
two ways out: `--consistency stop`, or the database's own dumps.

A restore takes a
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

## Backups sealed on this server's disk

A backup on the disk that holds it is readable by whoever gets the disk, a snapshot of it, a copy
of `/var/backups` or a retired drive. Destinations can already be encrypted (below); this seals the
copies that stay **on the server**:

```bash
noust backup encryption enable            # asks; shows the key once. Keep it somewhere else.
noust backup encryption status            # sealed or in clear, which key, how many
noust backup encryption show-key [ID]     # show a key again
noust backup encryption rotate            # a new key for what is made from now on
noust backup encryption import-key ID --passphrase-file FILE   # put back a key that was lost
noust backup encryption disable           # stop sealing; every key is kept
```

From then on every backup this server makes is written as an encrypted, authenticated file
(scrypt, AES-256-CBC through openssl, HMAC-SHA256 over the whole file: the construction of
[Noust's own state](#nousts-own-state)) **under the same name**: an application's archive, an
application's data, a database dump (whichever engine, a safety copy included) and the base backups
of [point-in-time recovery](databases.md#recovering-postgresql-to-an-instant). Sidecars stay
readable: they hold ids, sizes and digests, never the data.

- **One door.** Every reader of a backup file (verification, restore, the restore test, the drill,
  the put-back after a failed restore, extracting a base backup) opens it through
  `noust.core.backup_files.open_backup`, which authenticates the whole file before openssl reads a
  byte and gives the reader a decrypted copy in a private directory (`<backups>/.open`, 0700) that
  is removed when it is done, however it ends. A file that is not sealed is read where it is, so
  backups made before, dumps imported from elsewhere and copies downloaded in clear take the same
  path. An engine added later is sealed and opened without saying so (the guard is in the base
  class).
- **What is recorded is the stored file.** The size and the digest in a sidecar, its MAC, the dump
  record that is sent and checked against the destination are those of the sealed file, so
  everything that compares them keeps working. A push sends the sealed file as it is; a restore from
  a destination opens it the same way.
- **Keys.** Each key is a random 48-character passphrase in the secret store
  (`backups/local-encryption/<id>`), and its id is written in clear in the label of the files it
  sealed, so a rotation never strands a backup. **A sealed backup is gone with its key**: keep the
  key printed by `enable` where this server is not (a password manager, a safe). A backup whose key
  this server does not hold is refused with the command that puts it back, before anything is
  touched; so is one that was altered.
- **What it protects, and what it does not.** The key lives in the same server's secret store
  (root-only; sealed too when the store is sealed under a passphrase), so this defends the backups
  against being read **without** the secret store: an exposed directory or share, a snapshot or an
  rsync of `/var/backups`, a drive taken out when the store is not on it. It does not defend them
  against root on a running server, and it does not replace encrypting the disk. Backups made
  before it was turned on stay in clear until their retention removes them (`status` counts them).
- **What it costs.** Sealing and opening copy the file (sealing needs about two extra copies of it
  for a moment; opening, one). The database dumps staged *inside* an application's archive are not
  sealed on their own: the archive that carries them is, whole.
- **Not covered.** The write-ahead log archive of point-in-time recovery (the engine's own
  directory, written by PostgreSQL itself), Noust's own state (already an envelope under its own
  passphrase), and a dump handed to `noust db import`.

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

## Does a backup come back?

A backup nobody restored is a hope. `noust backup drill` rehearses it:

```bash
noust backup drill shop.example.com            # the newest backup; --backup ID for another
noust backup drills shop.example.com           # the reports kept, newest first
```

The backup is verified in depth, unpacked with the extractor a restore uses into a scratch directory,
and each database dump it carries is loaded into a temporary database of its engine, looked at and
dropped. An application that keeps its data in a SQLite file gets a step per file too: SQLite's own
`quick_check` runs on the unpacked copy, so a file that came back damaged fails the drill with SQLite's
words. Nothing of the application, its databases or its units is touched, and it is not started: the
report is dated and says what it proved (the files unpack, the data loads) and what it did not (that a
new server would serve them; a Redis snapshot, which replaces every key, and the dumps of a Compose
stack's own containers are named as not tried). It exits 1 when something fails. A scheduled backup runs
one by itself when the last good one is older than `backup.drill_days` (30 by default, `0` turns it off),
after the backup and its destinations, in the same run; a drill that fails is announced as a failed
backup and does not fail the backup's own run. `POST /api/apps/{domain}/restore-drills` (a job) and
`GET` the same path are the API, and the backup history drawer shows the reports. For a whole server,
`noust restore-server --plan` rehearses it, and the next section moves one.

## Provider guides

A destination is an rclone remote; the `--type` decides which. Object stores take the bucket (or
container) as the first part of the **folder**: `--field path=my-bucket/noust-backups`. Every
destination can be proved live with `noust backup destination test NAME`, which creates the folder (the first real write, so a key that can only read fails here)
and lists it, so that is the matrix that matters: a provider is
supported on your account when that command succeeds. Noust's own tests run offline against
rclone's interface; they pin what is passed to rclone for each type below, not how a provider
answers.

| Where | `--type` | What to give |
|---|---|---|
| Amazon S3 | `s3` | `provider=AWS`, `region`, the access key; no endpoint |
| Cloudflare R2 | `s3` | `provider=Cloudflare`, `endpoint=https://<account>.r2.cloudflarestorage.com` |
| Backblaze B2 | `b2` (native) or `s3` | `account` and application key; or `provider=Backblaze`, `endpoint=https://s3.<region>.backblazeb2.com` |
| Hetzner Object Storage | `s3` | `provider=Hetzner`, `endpoint=https://<location>.your-objectstorage.com` |
| Wasabi | `s3` | `provider=Wasabi`, `endpoint=https://s3.<region>.wasabisys.com` |
| DigitalOcean Spaces | `s3` | `provider=DigitalOcean`, `endpoint=https://<region>.digitaloceanspaces.com` |
| Scaleway | `s3` | `provider=Scaleway`, `endpoint=https://s3.<region>.scw.cloud`, `region` |
| IDrive e2 | `s3` | `provider=IDrive`, the endpoint of your bucket's region |
| MinIO | `s3` | `provider=Minio`, your own `endpoint` |
| Ceph (RadosGW) | `s3` | `provider=Ceph`, your own `endpoint` |
| Supabase Storage, Vultr and other S3 | `s3` | `provider=Other`, the `endpoint` they give you |
| Azure Blob Storage | `azureblob` | `account` and the account key |
| Google Cloud Storage | `gcs` | the JSON key of a service account (read from `--stdin`) |
| An SSH server | `sftp` | `host`, `user`, and a password or `key_file` |
| An FTP server | `ftp` | `host`, `user`, password, and `explicit_tls=true` (plain FTP sends the password in clear) |
| A NAS | `smb`, `webdav` or `sftp` | `host`, `user`, password |
| Nextcloud, ownCloud | `webdav` | `url`, `vendor`, `user`, an app password |
| Google Drive, OneDrive, Dropbox, Box, pCloud | `drive`, `onedrive`, `dropbox`, `box`, `pcloud` | the token `rclone authorize <type>` prints on your own computer |
| A disk this server mounts | `local` | an absolute `path` |

A provider name outside the list of the S3 form is passed to rclone as written (`provider=Alibaba`):
rclone knows more of them than the form offers. An address inside this machine's own networks (a NAS
on the LAN, a local MinIO) is refused until it is named in `security.allow_private_hosts`.

**Amazon S3 with the least access.** Give the key only what a backup needs; this policy is the whole of
it (replace the bucket):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::my-noust-backups"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::my-noust-backups/*"
    }
  ]
}
```

```bash
noust backup destination add offsite --type s3 \
  --field provider=AWS --field region=eu-west-1 --field access_key_id=AKIA... \
  --field path=my-noust-backups/server-1 --stdin < secret-access-key.txt
noust backup destination test offsite
```

**Cloudflare R2.** Create an API token with "Object Read & Write" on the bucket; R2 has no regions, so
leave `region` empty and use the account's endpoint.

**Google Cloud Storage.** Create a service account, give it *Storage Object Admin* on the bucket only,
download its JSON key and pass it whole: `--stdin < key.json`. `bucket_policy_only=true` is right for a
bucket with uniform bucket-level access, which is the default for new ones.

**Azure Blob Storage.** The container must exist; `--field path=container/noust-backups`. The account key
is under *Access keys* of the storage account.

**Backblaze.** The native `b2` type uses an application key limited to one bucket; the S3 type works with
the same key against the S3 endpoint of the bucket's region. Either is fine; native is one request fewer
per file.

What a provider does with a bucket's own lifecycle rules (versioning, object lock, expiry) is its
business and Noust's retention does not know about it: a lifecycle rule that deletes objects younger
than the schedule keeps them makes a backup disappear from under the schedule, and `noust backup
verify` is how you find out.

## Destinations are encrypted, and watched

A destination made without a word about encryption wraps the remote in rclone's `crypt` backend with a
key generated here and printed once by `noust backup destination show-key`: `noust backup destination
add --encrypt/--no-encrypt` (on by default), `encrypted` in the API (true, false or omitted), and
`backup.encrypt_new_destinations: false` turns the default off for a server that wants plain copies.
What the operator said wins; a key given to read backups already there means encryption; otherwise the
default. The `ens-medium` profile refuses an upload to an unencrypted destination whatever the default is.

The monitor announces a backup that was never taken, which fails nothing, and a destination that went
away, which fails only the next upload: `backup_missing` when no successful run of a schedule, a
database's policy, an application's data policy or Noust's own backup is newer than
`monitor.backup_missing_days` (2; a weekly or monthly schedule is given its period and a day; `0` turns it
off), once, then every day while it stays true; and `destination_unreachable` when a destination some
policy sends to does not answer (asked every `monitor.backup_check_hours`, as `noust backup destination
test` asks), once per outage with rclone's own words, and again when it answers. Both are switches under
`notifications.events`.

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

## Configuration

| Key | Default | |
|-----|---------|---|
| `databases.import_max_bytes` | 10 GiB | Largest dump the console accepts |
| `databases.import_directories` | none | Server directories `noust db import` may read from |
| `backup.encrypt_new_destinations` | `true` | Whether a destination made without a word about encryption is encrypted |
| `backup.drill_days` | `30` | A scheduled backup rehearses its restore when the last good drill is older than this; `0` turns it off |
| `backup.missing_days` | none | One number of days after which a backup that was not taken is announced, for every policy; `0` turns it off |

Permissions: the database import routes need `databases.manage`; volume and self backups use the
`backups.*` permissions, and changing the schedule of Noust's own backup is root-equivalent (sudo
mode). Everything is in the audit trail: `db.import`, `db.import.upload`, `volume_backup.*`,
`self_backup.*` and `restore_server.run`.
