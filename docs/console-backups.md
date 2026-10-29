# Backups

Part of [The console, page by page](console.md).

Five tabs: **Backups** (storage used per application, every backup: verify, restore, delete;
"New backup" with the same options as `noust backup create`), **Schedules**, **Volumes**,
**Noust** and **Destinations** (remote places, their test and their key). **Volumes** backs up an
application's data on its own: its persistent folders and the Docker volumes of its stack, picked
from what it declares (or everything it declares), on a schedule with its own retention, sent to
the same destinations, taken live, with the application paused or with it stopped, run by hand as
a job, and restored over what is there after you type the application's name (a safety copy of
what is there is taken first). **Noust** backs up Noust's own state (the store, the
configuration, the secrets and keys, the certificates and the unit files it generated) into one
file sealed with a passphrase: by hand (the passphrase is typed twice and kept nowhere) or on a
schedule (the passphrase is then kept in the secret store for the timer, and you keep a copy
outside the server), checked, and sent to a destination. A restore replaces the store the console
runs on, so it is never done from the browser: **How to restore** shows `noust self-backup
restore` and `noust restore-server --from`, and what they ask. **Move to another server** (on the same
tab) copies the applications, their data, the standalone databases and Noust's own state to a destination
the new server reaches: it shows what would be copied, what stops it and the engines the new server needs,
asks for the passphrase the state is sealed under (empty uses the stored one), queues the copy as a job and,
when it ends, shows each piece (a failed one in the manager's own words) and the commands to run on the new
server; nothing on this server is stopped or changed. A backup's history drawer lists the **restore tests**:
what was unpacked and loaded into a temporary database, when, and what it did not try. See
[disaster-recovery.md](disaster-recovery.md). The backup an update of a Docker
Compose stack takes first holds a dump of each database the stack runs, unless the application
turned that off under Settings > Automation; putting the databases back is `noust backup restore
BACKUP_ID --databases-only` from a terminal. Restoring only the files past a deployment that
changed the database schema asks first, as going back does (see
[above](console-applications.md#one-application)).
