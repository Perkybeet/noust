# Where backups go: destinations, encrypted

Part of [Backups and disaster recovery](disaster-recovery.md).

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

## The central, as a place for copies

On a central (`noust fleet`), the central itself is a place the copies of its servers can go, and the
one that needs nothing opened on the router. It is not a destination a server sends to (a server never
reaches back to its central: the tunnel goes one way, on purpose) but one the central *collects* from:
it asks each server, through the tunnel it already has, which copies it holds that it does not, and
downloads them one at a time, resumably, checking each against the checksum the server computed. A
server holds no credential of where its copies end up.

It is turned on in the console under **Backups > Destinations > The central**, for the whole fleet or
server by server, and from a terminal with `noust fleet collect enable`; the knobs (speed limit,
retention, room kept free, which kinds) and the copies held are in
[Taking the copies of your servers](central-maintain.md#taking-the-copies-of-your-servers). What is
collected counts as a copy away from the server in `noust health`, in the checks and in the ENS
profile, with its place named (`central:<name>`), when the digest the central gives for it is the one
this server recorded (the central's word that it keeps the file, which the server cannot see); it is
**one** copy, on one machine, so it does not
replace a destination outside it, and the console says so and offers to add one. Moving a copy
between two servers is not what it is for: it would pass the whole copy through the central's line.

## A night of bad network

A scheduled run takes its copy and sends it to each destination of its schedule. What happens when a
destination does not take it is decided in one place, and it is what turns a night of lost packets into one
message, or none:

- **Sending is patient; asking is not.** Sending a copy, fetching one, deleting by retention, and the
  listings and checks inside those, make three attempts with a pause that grows (30 seconds, then two
  minutes), wait a minute for the connection and call a transfer stuck after two minutes without data, within
  two hours for the whole operation, so one send never holds a run all night. Testing a destination, the
  monitor's probe and listing a destination for a person at the console keep one attempt and fifteen seconds,
  so a destination that is down says so at once. The journal of the run says every attempt, and the error
  that ends them says how many there were. What waiting does not mend (a refused credential, a bucket that is
  not there, a full quota) is tried once.
- **A copy that did not arrive is owed, not failed.** The run does not fail and says nothing yet: the copy
  stays on this server, written down with the destination it is owed to and what the destination said. It is
  tried again after a quarter of an hour, an hour and four hours, then every day, and the next scheduled run
  of the same thing sends what is owed first, before it takes its own copy. A server runs the pass with a
  timer (`noust-backup-resend`, every five minutes, made the first time a copy is owed); the central of a
  machine without systemd runs the same command with its own scheduler. `noust backup resend` runs it now
  (`--now` tries everything that is owed, not only what is due). A destination that does not answer ends the
  tries to it for that pass, the others go on.
- **A copy that is owed is never deleted to make room.** No retention of any kind of copy deletes a copy that
  is still owed to a destination, for fourteen days; past them the retention decides again (a disk cannot wait
  for ever) and says it in the audit trail (`backup.unsent_delete`). A schedule that keeps nothing on this server
  (`remote_only`) keeps the copy until every destination holds it. `noust backup list` says which copies were
  not sent, to where and when they are tried next, and the API gives the same in `pending_sends` on every copy.
- **It is said once for a destination, when it matters.** When a retry fails too, or at once when what the
  destination refused is of the kind waiting does not mend, one `backup_failed` notice names the destination,
  how many copies are owed and for which applications, what was tried, when it is tried next, where the copies
  are meanwhile and what the destination said, verbatim. Another failure to the same destination within
  `monitor.backup_missing_renotify_hours` (24) joins that message, and a reminder goes every window while
  copies are still owed. When everything has arrived a second message says so and answers the first on the
  channels that can.
- **The run says how it went.** A copy that is only waiting leaves the policy's last run as it was (ok): the
  backup was taken and the retry will see to the rest. A copy a destination refused for good (a credential, a
  quota) leaves it failed, with who refused it, because that needs somebody; the run still does not fail and the
  copy is still sent again if the destination mends.
- **The monitor does not say it again.** The runs of backups and of scheduled tasks announce their own failure,
  so the monitor does not add `unit_failed` for them when the run ended by its own exit; a run that systemd
  killed (its time limit, memory or a signal) said nothing and is still announced. The retention sweep and the
  archiving of a write-ahead log announce nothing themselves, and stay the monitor's.
- **A destination is said to be unreachable after three failed probes in a row**, five minutes apart while it
  is in doubt, and not when the runs have already said it (and the runs stay quiet when the monitor has).

The central that collects the copies of its servers (below) works the same way in its own terms: a transfer
retries a piece and resumes a cut file, whatever a server lists that the central does not hold is collected
at the next pass, and it tells you once per server, after five failed passes in a row (about half an hour of
its own backoff), or at once for a copy that is not being kept.

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
policy sends to does not answer three probes in a row (asked every `monitor.backup_check_hours`, and every
five minutes while it is in doubt, as `noust backup destination test` asks), once per outage with rclone's own
words, and again when it answers. Both are switches under
`notifications.events`.
