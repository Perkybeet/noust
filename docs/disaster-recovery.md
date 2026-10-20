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

### The roles of a PostgreSQL server

A dump names its owners and the roles it grants to and carries none of them, so one restored on a
new server lost its owners or failed on the first `ALTER ... OWNER TO`. An application backup now
also holds `pg_dumpall --globals-only` of each PostgreSQL server its dumps came from (once per
server), and a restore creates the roles the server lacks before it loads a dump. Only what is
missing is created: nothing that exists is altered, the superuser's password included, and the file
is read statement by statement, so a psql meta-command or anything that is not a role statement is
never run. What cannot be put back (a tablespace's directory, a setting for a database that is not
there) is listed. Noust's own state carries the same file, so `noust restore-server` has the roles
before the databases.

```bash
noust db globals dump [-o FILE]            # take them now (root-only file: it holds password hashes)
noust db globals list                      # the files this server has dumped
noust db globals restore FILE              # create the roles a file has and this server lacks
```

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

### When and how a backup runs

Every schedule (a database's, an application's, an application's data, Noust's own state) takes the
same options.

```bash
noust db backup-schedule set app_db -e postgresql --schedule daily --max-runtime 2h --timezone Europe/Madrid
noust backup schedule update shop.example.com --max-runtime 90m
noust backup priority show
noust backup priority set --nice 15 --io-class idle --cpu-quota 50
noust backup priority apply                # bring the units an earlier version wrote along
noust backup sweep                         # apply every policy's retention now
```

- **A time limit** (`--max-runtime`, `max_runtime_seconds` in the API; one minute to seven days)
  becomes `TimeoutStartSec=` in the service the schedule runs in: systemd stops what runs past it,
  the unit fails and the monitor announces it. It is not `RuntimeMaxSec=`, which systemd ignores in a
  `Type=oneshot` service and says so.
- **A time zone** (`--timezone`, `timezone` in the API) becomes the suffix of `OnCalendar=`
  (`*-*-* 02:00:00 Europe/Madrid`). It is checked against this server's own zone database. An update
  that names neither keeps what the timer had, as it does retention.
- **A priority.** A backup runs behind the applications: `Nice=10` and best-effort disk priority 7
  by default (`backup.nice`, `backup.io_class` `best-effort` or `idle`, `backup.io_priority`,
  `backup.cpu_quota` as a percentage of one CPU), written between two marker comments in the one
  place units are written, so a second pass changes nothing and a unit that is not Noust's is never
  touched.
- **Retention that does not wait.** A policy used to prune only after a run that went well, so a run
  that failed (a full disk, usually) pruned nothing. It now prunes after a run that could not take its
  copy, and a daily `noust-backup-sweep` timer applies every database, data and application policy
  without a run, reading the policies and the files and never the engines. The newest copy is kept,
  and when it failed its check the newest good one is kept too. A manual backup and a restore's
  safety copy are never a schedule's to delete. Destinations are pruned when a run sends to them.
- **A failed backup says all it was told.** The notice carries the end of the failure, bounded to
  what a chat message holds. The whole of what the tool said is kept (up to 512 KiB, scrubbed of the
  credentials Noust knows, the newest 200 failures) and the notice names it:
  `noust backup failures show 42` (`--raw` prints nothing but the output) and
  `GET /api/backups/failures/42/output`, which the notice links to when the console has a public
  address.

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
It also holds the web server's configuration (`/etc/nginx` and Apache's directory, each file with
its mode and its links, kept relative so Debian's `sites-enabled` links survive a different layout), so
a site edited by hand or the site of an adopted application comes back after a disaster, and the roles
of each PostgreSQL server. It does not hold the metrics and observation databases (they regenerate)
or application backups.

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
noust backup encryption seal-existing     # seal what was made before (below)
```

The same operations are `/api/backups/encryption`: `GET` (status), `POST /enable`, `/rotate` and
`/disable` (sudo mode), `GET /keys/{id}` (sudo mode and `secrets.reveal`; the value is shown, the
read is recorded without it), `POST /keys` (import; never echoed) and `/seal-existing` (a job).

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
  before it was turned on stay in clear until their retention removes them, or until you run
  `seal-existing` (`status` counts them).
- **Sealing what is already there.** `noust backup encryption seal-existing` seals the application
  archives, the data backups, the database dumps and the base backups of point-in-time recovery that
  were made in clear, in place and under the same names, and writes each record again with the
  stored file's size and digest (the sidecar and its MAC, the dump's record, the base's sidecar), so
  every check, restore and send goes on working. A backup that does not match its record is left in
  clear and said, never sealed into something that looks sound. A file that was sealed but whose record
  was not written, because the run stopped, is repaired the next time. An application that is being
  operated on is skipped and sealed by the next run. It needs room for one more copy of the largest
  backup for a moment, and it counts what is still in clear afterwards.
- **What it costs.** Sealing and opening copy the file (sealing needs about two extra copies of it
  for a moment; opening, one). The database dumps staged *inside* an application's archive are not
  sealed on their own: the archive that carries them is, whole.
- **Not covered.** The write-ahead log archive of point-in-time recovery (the engine's own
  directory, written by PostgreSQL itself one segment at a time), Noust's own state (already an
  envelope under its own passphrase), a dump handed to `noust db import`, files no record describes
  (a restore's safety copy, a dump put in the directory by hand) and what a destination already holds.

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
names, Docker when an application or a database instance needs it, and the database engines (apt and
zypper from the distribution's own repositories; a distribution that does not carry Docker is told how,
never given a third-party repository). Without it nothing is installed and the report names the
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
new server would serve them). Starting the application from its backup and passing its health gate is
not part of it, on purpose: the copy would run with the backup's `.env`, which points at the real
databases and services, and a copy that cannot reach them fails for the wrong reason, so it would need
a restored database of its own and an environment rewritten per application type. The dump of a database that lives in a Compose stack is loaded into a
throwaway container of **the image the service ran** (the dump's manifest records it): no network, no
published port, a memory and process limit, a name `noust-drill-<random>` and the label `noust.drill=1`,
a password made for the occasion that reaches Docker through its environment and never an argument, and
removed by its exact name, with its volume, whatever happened; what it holds is counted. A Redis or Valkey
snapshot, which replaces every key and so cannot be tried beside the real data, is loaded into a second
server that listens on no port but a private socket and is stopped afterwards. It exits 1 when something
fails. A scheduled backup runs
one by itself when the last good one is older than `backup.drill_days` (30 by default, `0` turns it off),
after the backup and its destinations, in the same run; a drill that fails is announced as a failed
backup and does not fail the backup's own run. `POST /api/apps/{domain}/restore-drills` (a job) and
`GET` the same path are the API, and the backup history drawer shows the reports. For a whole server,
`noust restore-server --plan` rehearses it, and the next section moves one.

## Provider guides

A destination is an rclone remote; the `--type` decides which. Object stores take the bucket (or
container) as the first part of the **folder**: `--field path=my-bucket/noust-backups`. A destination
is tried when it is saved: `noust backup destination add` and `update` (and `POST` and `PUT
/api/backup-destinations`) create the folder, which is the first real write so a key that can only
read fails here, and list it, exactly as `noust backup destination test NAME` does, and keep nothing
that does not answer. `--skip-test` (`"verify": false` in the API) saves one that is down on purpose.
For an object store the **bucket must already exist**: Noust looks at it first, read only, and says
so, instead of creating a bucket in your account out of a mistyped name. So the matrix that matters
is your own: a provider is supported on your account when `destination add` succeeds. Noust's offline
tests pin what is passed to rclone for each type below, not how a provider answers; the next
subsection says what has been run against a real server.

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

### What has been tried against the real service

The table above says what Noust passes to rclone. This one says what has been run against a real
server, when, and with which rclone. A row is added when its run is, with the date it was run:

| Where | Last tried | rclone | What ran |
|---|---|---|---|
| MinIO (`s3`, `provider=Minio`) | 2026-10-17 | 1.60.1 | A bucket that does not exist is refused and not created; a wrong key is refused with the server's own words and nothing is kept; a name MinIO would refuse is stopped before the network; a file goes up, is listed, comes back identical and is deleted, in clear and encrypted |
| An SSH server (`sftp`) | 2026-10-17 | 1.60.1 | Against rclone's own SFTP server: tried and kept, a wrong password refused and not kept, a file sent (verified by MD5), listed, fetched back identical and deleted |
| A WebDAV server (`webdav`, `vendor=other`) | 2026-10-17 | 1.60.1 | Against rclone's own WebDAV server, the same steps, the upload verified by size |

The rclone is the 1.60.1 package of the harness image (`v1.60.1-DEV`); a newer one is expected to
behave the same and has not been tried. The SFTP and WebDAV rows prove Noust's side of the
protocol (what it passes, what it keeps, what it refuses) against rclone's server for it, which is a
different implementation from the client's side; they do not prove a particular vendor's quirks.

**Not tried against the real service**, and so supported as far as rclone documents it and no
further: Amazon S3, Cloudflare R2, Backblaze B2, Hetzner Object Storage, Wasabi, DigitalOcean
Spaces, Scaleway, IDrive e2, Ceph, Supabase Storage and Vultr, Azure Blob Storage, Google Cloud
Storage, an FTP server, SMB, Nextcloud and ownCloud, Google Drive, OneDrive, Dropbox, Box, pCloud.
Prove yours with `noust backup destination add`; if it connects, the same steps ran.

The runs are `python tests/integration/backups_run.py --scenario s3_destination` and `--scenario
protocol_destinations` (Docker; they create `noust-test-w6b-*` containers with the label `noust.test=1`
and remove them).

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
