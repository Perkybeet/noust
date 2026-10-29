# Back it up, and prove the backup comes back

**Goal:** a backup of your application and its database every night, a copy of each one off the
server, encrypted, and a rehearsal that shows it restores.

**Time:** five minutes.

**You need:** [a deployed application](first-application.md), ideally
[with a database](first-database.md), and for the copy off the server, an account at an object
store or an SSH server you can write to. The example uses Backblaze B2.

The examples use Kelmoor's application `wrenfield.io` on its server `fra-1`.

## Steps

1. **Take a backup now.**

   ```bash
   noust backup create wrenfield.io -m "Before the launch" --include-databases
   ```

   A backup is one file under `/var/backups/noust/wrenfield-io/`, with its checksum. It holds the
   release that serves, the `.env` and a dump of each database the application uses, so it
   restores on a server that knows nothing about this one.

2. **See it.**

   ```bash
   noust backup list wrenfield.io
   ```

   Each line starts with the backup's id, such as `wrenfield-io_20261029_181502`, which every
   other backup command takes.

3. **Back it up every night.**

   ```bash
   noust backup schedule create wrenfield.io --schedule daily --retention-count 7
   ```

   A systemd timer runs it, dumps the databases with it, and keeps the seven newest scheduled
   backups. It never deletes a backup you took by hand.

4. **Add a place off the server.** A backup on the same disk is lost with the disk. Create a
   bucket at your provider first, then:

   ```bash
   noust backup destination add offsite --type b2 \
     --field account=0042a1b2c3d4e5f0000000001 \
     --field path=kelmoor-backups/fra-1 \
     --prompt
   ```

   `--prompt` asks for the application key twice, without showing it; it never goes on the
   command line. Noust tries the destination before saving it, and encrypts everything it sends
   there. Give each server a folder of its own (`kelmoor-backups/fra-1`).

5. **Keep the encryption key somewhere else.**

   ```bash
   noust backup destination show-key offsite
   ```

   Store what it prints in your password manager. Without it, nobody can read the backups on that
   destination, this server included.

6. **Send every scheduled backup there.**

   ```bash
   noust backup schedule update wrenfield.io --schedule daily --destination offsite
   ```

7. **Rehearse a restore.**

   ```bash
   noust backup drill wrenfield.io
   ```

   The drill verifies the newest backup, unpacks it the way a restore would, and loads each
   database dump into a temporary database that it then drops. Nothing of the application is
   touched. A schedule runs one on its own every 30 days.

In the console, the same is under **Backups**: the list, **Schedules**, **Destinations** and, on
each backup, **Restore**.

## What you should see

- `noust backup list wrenfield.io` shows your backup with its size and description.
- `noust backup schedule list` shows the schedule and when it last ran.
- `noust backup drill wrenfield.io` ends without an error and keeps its report:
  `noust backup drills` lists every drill.

## Restoring for real

```bash
noust backup restore wrenfield-io_20261029_181502
```

It replaces the application's files with the ones in the backup, after asking. To restore from the
copy off the server, on this server or a new one, add `--from offsite`. To bring back a whole
server, see [Bringing a whole server back](backups-restore-server.md#bringing-a-whole-server-back).

A restore puts the application's files back, not its database: the dumps in the archive are loaded
with their own command, `noust db import` ([Databases](disaster-recovery.md#databases)), because
putting an older database under newer code is a decision. (A Docker Compose stack is the
exception: its databases come back with its files.) [Ship a change, and roll it
back](first-rollback.md) says how Noust guards the code side.

## If it fails

- **The destination is not saved:** Noust tried it and it did not answer, or the bucket does not
  exist (it never creates one). The error is rclone's own; fix the field it names and run the
  command again.
- **A scheduled backup failed:** `noust backup failures` prints each failure's whole output. Set up
  [notifications](first-safety.md) so the next one reaches you.
- **The drill failed:** the report says which step: the checksum, unpacking, or loading a dump.
  The backup is not one to rely on; take another and drill it.

[Backups and disaster recovery](disaster-recovery.md) covers the database schedules, volumes,
Noust's own state and every provider.

## Next

[Ship a change, and roll it back](first-rollback.md).
