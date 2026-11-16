# Secrets, databases and backups

Part of [Security](security.md).

## Secrets at rest

Noust keeps a secret in a file only root can read, beside the data it belongs to. Two kinds are also
**encrypted** under a key in a file of its own (`/etc/noust/secrets.key`, 0600): the credentials of
the backup destinations and the secrets of the notification channels in `config.yaml` (webhook URLs,
bot tokens, the SMTP password, a mail provider's key, a webhook's headers). That protects against a
copy: a `config.yaml` pasted in a ticket or kept in a backup that left the key out discloses nothing.
It is not a protection against root on the same machine, who reads the key as well, and Noust does not
say it is. The rest of the guarantee is about the copies too:

- **Where they live.** Credentials Noust holds for itself (a registry's password, a backup
  destination's key, a GitHub App's private key, a node's token) are one 0600 file each under
  `secrets/` beside the store, and the database holds only their names. `/etc/noust/config.yaml`
  holds the few that are configuration: the notification channels' secrets encrypted as said, and
  the database administrators' passwords as text, 0600 as well. `noust config protect` encrypts
  what a file written before 4.0 still holds as text (the console does it when it starts), and
  losing `secrets.key` makes those channels unreadable until their secrets are entered again.
  A central can seal the secret store under a passphrase (`docs/CENTRAL.md`).
- **They do not travel.** No read of the API returns one (the configuration, the variables and the
  exports show `***`; what reveals a secret on purpose needs a name or a parameter, a permission
  and sudo mode, and leaves an audit event); `noust app export` leaves them out unless
  `--with-secrets`; `noust store export` masks the secret variables of applications and services
  and the credentials of source addresses unless `--with-secrets` (audited as
  `store.export.secrets`); the evidence package of an incident carries a copy of the store with the
  columns that hold a secret value emptied, and the copy is rewritten so the old values are not left
  in its free pages.
- **It is tested, and keeps being.** `tests/test_secrets_at_rest.py` plants a secret in every place
  Noust keeps one, reads every route of the API that needs no name, and fails if any answer holds one,
  so a route added later is covered when it is added. The list of columns that hold a secret value
  is `noust.core.store_secrets`; a test reads the real schema and fails on a column that reads as a
  secret (or holds a value under another name) that nobody has ruled on, so a table another area adds
  cannot put a credential into a snapshot unnoticed.

What this does not do: a backup of the application (`.env`, database dumps) is a secret by design and
is protected by being encrypted on the way out (below); `noust config show` and
`noust store export --with-secrets` print secrets in clear to the root who asks.

## Databases

The console's SQL runner is read-only unless told otherwise, and that is enforced by the
database server, not by inspecting the statement:

- **PostgreSQL**: the statement runs in a `READ ONLY` transaction in a session that signs in
  over `127.0.0.1`, with a password, as a dedicated role `noust_ro_<database>`: not a
  superuser, `default_transaction_read_only` on, granted only `CONNECT`, `USAGE` and `SELECT`.
  Because the login itself is the limit, `SET ROLE`, `RESET ROLE`, `set_config('role', ...)`
  and `SET SESSION AUTHORIZATION` have nothing to return to, and the server refuses
  superuser-only functions such as `pg_read_file` and `COPY ... TO PROGRAM`. The password is
  random, set as a SCRAM verifier, and kept in a 0600 root-owned file under
  `/var/lib/noust/secrets/postgresql/`. `pg_hba.conf` must allow
  `host <database> noust_ro_<database> 127.0.0.1/32 scram-sha-256` (the Debian and Ubuntu
  default `host all all 127.0.0.1/32` line does); if it does not, read mode fails with that
  line in the error and never falls back to the superuser.
- **MySQL and MariaDB**: the statement runs in a `READ ONLY` transaction as a dedicated
  account with `SELECT` on that one database and nothing else (no `FILE`, so no
  `LOAD_FILE()` or `INTO OUTFILE`). Its password is rotated on every call and never stored.
- **MongoDB and Redis** have no read-only mode; every statement is a write.

A write needs `databases.write` and sudo mode (and, under the ENS profile, `root_equivalent`:
it runs as the engine's superuser); `EXPLAIN ANALYZE` executes its statement and counts as a
write. With approvals on, a write is a four-eyes request. The server enforces a statement
timeout and bounds the output. Only one statement is accepted per request in read mode. Every
statement is an audit event: a read by its digest, a write with its text, secrets scrubbed.
(Up to 3.0 the text went to a logger that had no handler, so it was recorded nowhere.)

The data browser (tables, rows, Redis keys) reads as the same read-only account. Editing a row
changes exactly one row, found by its whole primary key, needs `databases.write` and sudo mode,
and is a four-eyes request when approvals are on. Restoring over an existing database takes a
safety copy first and puts it back if the restore fails; `--as-new` restores beside it.

The statement reaches the client as data, never as a script: psql gets it as a `-c` string
(a leading backslash is refused) and mysql reads it in `--binary-mode` with its client
commands (`system`, `\!`, `source`, `tee`, `pager`) refused, so no shell escape is reachable
from the console.

Restoring a dump runs its SQL with the engine's administrative account, so a restore trusts
the dump's SQL; it needs sudo mode and is audited. The client never reads the dump as a
script: a PostgreSQL plain dump with psql meta-commands (`\!`, `\o`, `\set`, `\connect`)
outside its COPY data is refused before anything is dropped, pg_dump's own
`\restrict`/`\unrestrict` excepted; MySQL and MariaDB read the dump on stdin in
`--binary-mode`, where `system`, `\!` and `source` are off.

## Backups

A backup holds the application's `.env` and its database dumps, so it is a secret wherever it
goes (ENS mp.si.2, mp.info.6):

- **Integrity.** Each backup's metadata sidecar (`<id>.json`) carries an HMAC-SHA256 under a key
  kept in the secret store, not beside the backups. The MAC covers the archive's SHA-256, so an
  archive and a sidecar replaced together on the backup storage or on a remote are caught by
  `noust backup verify`, which also says when a backup predates the MAC or was signed by
  another server.
- **Encryption on the way out.** Under `security.profile: ens-medium`, or with
  `backup.encryption: required`, no backup or database dump is uploaded to a destination that is
  not encrypted: the refusal comes before rclone runs, at the one place every upload passes.
  Encrypt a destination with `noust backup destination update <name> --encrypt` and keep its key
  (`show-key`) apart from the server. `noust backup destination add` encrypts every remote
  destination unless you pass `--no-encrypt` (a `local` one, a disk this server mounts, is not),
  and `noust health` names the remote destinations made before that, or without it, that still
  send backups in clear.
- **Proven restorable.** A scheduled backup deep-verifies what it took (the archive unpacked with
  the extractor a restore uses) whenever the last good verification is older than
  `backup.verify_days` (7; the ENS profile never allows longer), and sends nothing that fails.
  Every verification, from any caller, is a `backups.verify` audit event.
- **The central itself.** `noust central backup` writes the configuration, the store (a
  consistent SQLite snapshot taken while it runs), the secrets and the keys into one file,
  encrypted and authenticated under a passphrase that is typed and never stored (scrypt,
  AES-256-CBC by openssl, HMAC-SHA256, the construction of sealed secrets). `--verify` proves a
  copy against its manifest; `--decrypt` gives the archive back for a restore.
