# Extensions, and dumps from elsewhere

Part of [Databases](databases.md).

## PostgreSQL extensions

An extension is loaded into **one database** with `CREATE EXTENSION`; which ones can be loaded depends on
what the server has on disk (the host's packages, an instance's image), so Noust reads the server's own list
(`pg_available_extensions`) and never keeps a catalogue of its own.

```bash
noust db extension list shop_db -e postgresql            # what is loaded
noust db extension list shop_db -e postgresql --all      # everything the server offers, trusted or not
noust db extension add shop_db vector -e postgresql@vec
noust db extension add shop_db postgis_topology -e postgresql@geo --cascade   # and the extensions it needs
noust db extension remove shop_db vector -e postgresql@vec                    # asks; --cascade drops what depends on it
```

- **The name is the server's.** What is asked for must be a name the list returns, and it reaches the statement
  quoted as an identifier; an extension the server does not offer is refused, saying where one comes from (on the
  host, the extension's package first: `apt install postgresql-17-<extension>`; on an instance, an image that has
  it). Nothing is sent to PostgreSQL before that.
- **It runs as the cluster's superuser** and is recorded in the audit trail (`db.extension`). Most extensions that
  matter are not *trusted* (PostgreSQL lets a database owner load only the ones whose control file says so), and
  loading one runs its script with the superuser's rights: the API therefore asks for the whole server's scope for
  an extension that is not trusted, and a team member loads only the trusted ones.
- **PostgreSQL only,** and never in PostgreSQL's own databases (`postgres`, `template0`, `template1`). Removing
  an extension without `--cascade` is refused by PostgreSQL while anything depends on it, and says what.

The console shows the loaded extensions on the database's Overview and loads or unloads one from there. The API
is `GET /api/databases/databases/{engine}/{name}/extensions`, `POST` to the same path (`name`, `cascade`) and
`DELETE .../extensions/{extension}?cascade=` (sudo mode), with `databases.read` and `databases.manage`.

## A dump from elsewhere, and a whole server's databases

A dump is a script. `COPY ... FROM PROGRAM` runs a program for whoever loads it, and a superuser's
session runs it as the `postgres` account. So Noust does not give a dump the superuser unless you say
so.

```bash
noust db import shop /srv/dumps/shop.sql.gz -e postgresql          # as the database's owner
noust db import shop /srv/dumps/shop.sql.gz -e postgresql --as-superuser     # the explicit way
noust db restore shop /srv/dumps/shop.sql.gz -e postgresql --as-superuser    # a file, not a dump of Noust's
```

- **PostgreSQL loads a dump from outside as the database's owner.** The load signs in as a role made for
  it (not a superuser, member of the owner, `VALID UNTIL` soon, dropped when it is done or swept by the
  next load if the process died), so the server itself refuses what the owner may not do: `COPY ...
  FROM PROGRAM`, an extension that needs a superuser, reading a server file. A script cannot hand the
  superuser back from inside (`RESET ROLE` and `SET SESSION AUTHORIZATION` lead back to that role, not
  to `postgres`). A database owned by the superuser (one made without an owner) is loaded by a login
  that owns what it makes and hands it to the real owner afterwards. It signs in over `127.0.0.1` like
  the read-only console, so `pg_hba.conf` needs the line the distribution's default has (`host all all
  127.0.0.1/32 scram-sha-256`); when it does not, the error says so and says how to repeat the load
  with `--as-superuser`.
- **A refusal for want of rights says how to repeat it.** The server's own words come first, then the
  way to load it as the superuser. `--as-superuser` asks twice on the command line; in the API it is
  `as_superuser` with `confirm_superuser`, and sudo mode. It is on the audit trail
  (`db.restore.superuser`). Use it for a dump you made or have read. With it no `SET ROLE` is written
  into the script, so what the dump creates belongs to the superuser unless it names owners
  (`--keep-owners`) or you run `noust db fix-owner` afterwards.
- **Noust's own dumps go back as they were.** A backup restored by name, a download from a destination, the
  safety copy put back after a failed restore, a database moved between engines and an application
  backup's database are loaded as the superuser, because what is in them is what Noust took out of the
  database (extensions that need a superuser, objects of several owners). So is a dump that sits in
  the engine's own folder when you name it with `noust db restore`.
- **MySQL, MariaDB and MongoDB are loaded as the administrative account.** Their dumps are checked for
  the statements that leave the database (`USE`, `CREATE DATABASE`) but run with that account's rights;
  `--as-superuser` is refused for them. Import only dumps you trust into those engines.

### Every database of a server, and its accounts

`pg_dumpall` writes the accounts and every database in one file. `noust db import-all` brings it in:

```bash
noust db import-all /srv/dumps/all.sql.gz -e postgresql --plan            # what would happen
noust db import-all /srv/dumps/all.sql.gz -e postgresql                   # the databases
noust db import-all /srv/dumps/all.sql.gz -e postgresql --users           # and the accounts
noust db import-all /srv/dumps/all.sql.gz -e postgresql --users --replace -d shop
```

- **Read, never run.** The file is cut where psql would read each `\connect` (a row, a string or a
  function body that looks like one is data). The accounts become data and the statements that create
  them are written by Noust from it, so a role named `x; DROP ...` is only a name that is not used.
  Each database's part is an ordinary dump that goes through the import above, with its own check, its
  own safety copy and its own way back.
- **The accounts only when you ask** (`--users`, `restore_users` in the API), after the plan shows them
  and you accept. They keep the password hash the dump holds (the old password signs in), but not the
  power the source gave them: superuser, `CREATEROLE`, replication and row-security bypass are left out
  and listed. An account that exists is never touched, `postgres` and the server's own roles are not
  created, and a membership is kept only between accounts made now. Per-role settings, tablespaces and
  `DROP` statements are listed as not carried.
- **Owners and privileges** are kept for the roles that exist and left out, statement by statement, for
  the rest (the source's `postgres`): a database that is created is owned by the account that owned it
  at the source, one that is replaced is recreated with it, and one that is loaded over is handed to it
  afterwards. Objects owned by another account than the database's go to the database's owner: only the
  owner can be handed an object by a loader that is not a superuser. `--as-superuser` keeps them all.
- **Databases fail alone.** One that does not load is told with the engine's own words and dropped again
  if this created it; the others load, and the exit status is 1.
- **The server's own databases** (`postgres`, `template1`) are loaded only when named (`-d postgres`).

The API is `POST /api/databases/imports/cluster/plan` (reads the file, changes nothing) and
`POST /api/databases/imports/cluster` (a job with sudo mode; `restore_users` needs `confirm_users`).
The upload and the path rules are the single import's, and a file that holds one database is refused with
where to go.

**MySQL and MariaDB.** `mysqldump --all-databases` (or `--databases`) is read the same way: each
user database becomes a file of its own, both of mysqldump's passes included (a view is written twice,
under a second `-- Current Database` marker), without the `CREATE DATABASE` and `USE` that name it, with
the header the dump set up and the footer it restored; the database is made with the character set the
dump gave it. The server's own schemas (`mysql`, `sys`) are never loaded: `mysql` holds the server's
accounts and grants and loading it would overwrite them, root included. The accounts come from a file
of their own, because a `mysqldump` carries them as table data, not statements: `--accounts-file`
(`accounts_upload_id` or `accounts_path` in the API) takes the `CREATE USER` and `GRANT` lines that
`SHOW CREATE USER` and `SHOW GRANTS` print (or `pt-show-grants`), one per line. They are read into data
and written again by Noust: a password only as the `mysql_native_password` hash the server stores (the
old password signs in), privileges only on a database or a table and only those about its data (never
`FILE`, `SUPER`, `PROCESS`, `RELOAD`, `CREATE USER`, replication, a global grant or `WITH GRANT OPTION`:
they are listed as not carried), `ACCOUNT LOCK` kept. A `caching_sha2_password` hash (MySQL 8.4's
default) holds raw bytes a text file cannot carry exactly, so such an account is made **locked**, for you
to give a password with `noust db user-password`. Accounts are made first and their privileges after the
databases are loaded, because a grant on a table needs the table; one that exists is never touched.
Unlike PostgreSQL, a MySQL or MariaDB dump is loaded with the administrative account (its views,
triggers and routines are written with a `DEFINER` of the source's accounts, which an unprivileged login
could not create), so import only dumps you trust into them.

Tested against a real PostgreSQL 16 (`tests/integration/engines_run.py`, scenarios
`restore_unprivileged` and `import_cluster_postgresql`): the server refuses `COPY ... FROM PROGRAM`,
`RESET ROLE` and `SET SESSION AUTHORIZATION`, nothing runs and the database is put back, an ordinary
dump's objects belong to the owner, an extension that needs a superuser needs `--as-superuser`, a
dump of all databases brings the accounts with the passwords they had and none a superuser, a hostile
database of the file fails alone, and no login of the loader is left.
