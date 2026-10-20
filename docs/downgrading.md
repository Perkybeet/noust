# Going back to an earlier Noust

An upgrade can be undone. What decides how is one fact: **Noust's store only moves forward.** The
store (`noust.db`, in `/var/lib/noust`) records which applications exist, how they deploy, the
accounts, the destinations and the schedules. Each release opens a store written at its own
schema version or an older one, migrating the older ones, and **refuses a store written by a newer
release** instead of guessing:

```
The Noust database at /var/lib/noust/noust.db was written by a newer Noust
(schema v14; this version understands up to v13)
```

So going back is one of two things, and one command says which:

```bash
noust store compat --to 3.3.1
```

```
Store: /var/lib/noust/noust.db
Schema on disk: 14
This release writes: 14
Oldest release that opens it: 3.4.0
Copies kept before upgrading:
  noust.db.v13-20261014T101500Z-4242-ab12cd.bak  schema 13  2026-10-14 10:15 UTC

1. Noust 3.3.1 cannot open this store (schema 14; it understands up to 13). The copy kept before
   the upgrade can be opened: noust.db.v13-..., schema 13, taken 2026-10-14 10:15 UTC.
...
```

`--json` prints the same as data. It reads and changes nothing.

## Which release opens which store

| Store schema | First written by | Opened by |
|---|---|---|
| 8 | 2.0.0 | 2.0.0 and later |
| 9 | 2.1.0 | 2.1.0 and later |
| 10 | 2.2.0 | 2.2.0 and later |
| 11 | 3.0.0 | 3.0.0 and later |
| 12 | 3.1.0 | 3.1.0 and later |
| 13 | 3.2.0 | 3.2.0 and later (3.3 did not change it) |
| 14 | 3.4.0 | 3.4.0 and later |

The table is in the code (`noust.core.store_compat.SCHEMA_RELEASES`), and a test fails when the
schema a release writes has no row in it. **A schema only ever changes in a minor release**
([release policy](release-policy.md)): a patch release can always be replaced by the one before it.

## The release can open the store: install it

Going from 3.3.2 to 3.3.1, or from any release to an earlier one with the same schema, needs
nothing but the earlier package. **The repositories serve only the newest version of the package**
(each distribution's repository is rebuilt for every release), so the package you go back to is
one you saved, or one you fetch:

```bash
# Before upgrading, keep the package you are on:
apt-get download noust            # Debian, Ubuntu: noust_3.3.1-1_all.deb in this folder
dnf download noust                # Fedora: needs dnf-plugins-core

# Going back:
apt install --allow-downgrades ./noust_3.3.1-*_all.deb      # Debian, Ubuntu
dnf downgrade ./noust-3.3.1-*.noarch.rpm                     # Fedora, the package you saved
zypper install --oldpackage ./noust-3.3.1-*.noarch.rpm       # openSUSE, the package you saved
pip install 'noust[all]==3.3.1'                              # PyPI keeps every version
```

Each release's `.deb` is also attached to its page on GitHub, and PyPI keeps every version, so a
Debian, Ubuntu or pip server can always fetch it. Then `systemctl restart noust-web noust-monitor`.
Nothing else is touched: applications, units and sites are not part of Noust's own version, and
they keep serving the whole time.

## The release cannot open the store: put the copy back

Every upgrade that has to migrate the store first keeps a copy of it beside the file, taken with
SQLite's online backup (so what the write-ahead log still held is in it), owner-only, named by the
schema it holds: `noust.db.v13-<time>-<pid>-<id>.bak`. Going back to a release that predates the
schema means going back to that copy, **and losing what changed in Noust since it was taken**
(applications created, accounts, settings). Applications' own data, their units and sites, are
unaffected.

`noust store compat --to RELEASE` gives the exact steps for your server. In outline:

1. Stop the console and the monitor: `systemctl stop noust-web noust-monitor`.
2. Keep the current store aside, with its write-ahead log (`noust.db`, `noust.db-wal`,
   `noust.db-shm`), in a folder of its own, so nothing is lost if the decision turns out wrong.
3. Put the copy in the store's place: `cp noust.db.v13-... noust.db && chmod 600 noust.db`.
4. Install the earlier release (above).
5. Start the console: `systemctl start noust-web noust-monitor`.

If no copy was kept (a store from before copies were made), the way back is a self-backup taken
before the upgrade: `noust self-backup list`, then `noust self-backup restore`
([disaster-recovery.md](disaster-recovery.md)). Take one before every minor upgrade:
the update refuses nothing for lack of one, but it says so ("There is no self-backup of the
configuration and the store on this server").

## Before you upgrade

Noust rehearses its own update before it installs anything: it checks the room on disk, that the
store opens and passes SQLite's integrity check, what the package manager says when it simulates
the upgrade (`apt-get -s`, `dnf` in test mode, `zypper --dry-run`) and whether a recent self-backup
exists. A failed check stops the update with the tool's own words; a warning is shown and the update
goes on. The same checks run when a central updates its servers.

## What 4.0 wrote that an earlier release ignores

- **Sealed channel secrets.** From 4.0 the credentials of the notification channels (a webhook URL, a
  token, the signing secret, the headers) are kept in `/etc/noust/config.yaml` as `noust-channel:v1:`
  values, sealed under `/etc/noust/secrets.key`. An earlier Noust reads them as the text they are, so
  its channels would try to deliver to the sealed value and fail. Going back means opening them
  first: keep the key with the file, and set the credentials again with the earlier release
  (`noust config set notifications.channels.webhook.webhook_url ...`), or restore `config.yaml` from a
  backup taken before the upgrade.
- **`domains.enabled`** (schema v14) and the monitor's tables: the older release ignores the columns,
  and a name that was switched off is served again by it.

## What an upgrade guide says about it

Each minor's upgrade guide has a "Rolling back" section that is specific to what that release
changed: [3.3](UPGRADING-3.3.md#rolling-back), [3.2](UPGRADING-3.2.md#rolling-back),
[3.1](UPGRADING-3.1.md#rolling-back). Read it first: it names the files the release wrote that the
older one ignores.
