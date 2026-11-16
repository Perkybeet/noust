# TLS and read replicas

Part of [Databases](databases.md).

## TLS for an instance, and for a link

An instance can serve TLS with a certificate signed by an authority of Noust's own, and an application
can be told to verify it.

```bash
noust db instance tls pg16                           # how it serves TLS now, and its certificate
noust db instance tls pg16 --mode optional --plan    # what the restart would do
noust db instance tls pg16 --mode required           # asks, restarts the engine on TLS
noust db instance renew-tls pg16                     # a new certificate
noust db instance tls-ca                             # the authority applications trust
noust db link shop.example.com shop_db --engine postgresql@pg16 --tls verify-full
```

- **Modes.** `off`; `optional` (a client that asks for TLS gets it); `required` (nothing else gets in:
  PostgreSQL refuses a connection that is not TLS, MySQL and MariaDB set `require_secure_transport`).
  Noust's own client inside the container keeps connecting through the engine's local socket, so
  `required` never locks Noust out. MongoDB serves TLS to those who ask (`optional`) and cannot refuse
  the rest; MariaDB can refuse plaintext from 11.4. Redis, Valkey and KeyDB serve TLS on a port of
  their own, so Noust gives them one mode, `required`: the instance's one published port speaks TLS and
  the plaintext port is shut (`optional` would need a second published port, and is refused with that
  reason). Noust's own client inside the container, its health check, the key browser and the backups
  are told to speak it, so everything goes on working; a link takes a TLS mode and the application
  gets a `rediss://` URL with redis-py's `ssl_cert_reqs`, `ssl_ca_certs` and `ssl_check_hostname`
  (`DB_SSL_MODE` and `DB_SSL_CA` with `--extra-vars`). A link without a mode to such an instance is
  refused: its URL could not connect. Making such a database public no longer asks for
  `--allow-plaintext`, because the port is TLS-only already. A mode the engine cannot serve is refused,
  naming why, before anything changes. ClickHouse is not offered yet.
- **The authority.** The first instance that serves TLS makes it: a P-256 key and a certificate in
  `/etc/noust/tls` (the key readable by root only). The certificate of an instance carries the names it
  is reached by (`localhost`, `127.0.0.1`, `::1`, its name, its container name and the server's
  hostname) and is valid for 825 days. Noust says on `noust db instance tls` and in the console when it
  expires, and `renew-tls` signs it again. Every key is written by Noust, through its filesystem seam,
  and is never on a command line.
- **Changing the mode restarts the engine** and waits for it to answer. When it does not, the previous
  configuration is put back, the instance runs as it did, and the error carries the container's own
  log. `--plan` lists the applications that lose their connection for the length of the restart.
- **A link may say how the application treats the certificate** (`--tls`, or *Connection security* in
  the console): `require` encrypts and checks nothing about who answers; `verify-ca` also checks that
  the authority signed it; `verify-full` also checks the name. Noust writes the client library's own
  words into the connection string (`sslmode=verify-full&sslrootcert=/etc/noust/tls/ca.crt` for
  PostgreSQL, `ssl-mode=VERIFY_IDENTITY&ssl-ca=...` for MySQL and MariaDB, `tls=true&tlsCAFile=...` for
  MongoDB) and, with `--extra-vars`, in `DB_SSL_MODE` and `DB_SSL_CA`. Only the authority's *public*
  certificate is handed over. The host's own engine can be asked to `require` and nothing more, because
  its certificate is its package's and nothing can verify it; to verify, move the database to an
  instance (`noust db move`). A link to an instance that does not serve TLS is refused with the command
  that turns it on.
- **It follows the database.** A move or a migration keeps a link's mode, refuses a target that cannot
  keep it, and a migration from the host's engine makes the new instance serve TLS (`optional`) when an
  application connects with it. A migration from an instance that serves TLS makes one that serves it
  in the same mode.

The API is `GET /api/databases/instances/{name}/tls`, `POST .../tls/plan`, `PUT .../tls` and
`POST .../tls/renew` (sudo mode, jobs), `GET /api/databases/instances/tls-authority`, and `tls` in the
body of `POST /api/apps/{domain}/databases/link`. The routes are the server's and need `databases.read`
or `databases.manage`.

## A host engine with a certificate clients can verify

A database made public is reached through a proxy that passes bytes, so the certificate the client
sees is the engine's own, and a package leaves a self-signed one: a client can encrypt but cannot know
who answers. `noust db tls` gives PostgreSQL, MySQL or MariaDB on the host a real certificate (an
ACME lineage, or one you uploaded) so `sslmode=verify-full` works from anywhere, with nothing
terminating TLS in between.

```bash
noust db tls enable -e postgresql --host db.example.com             # the lineage named db.example.com
noust db tls enable -e mysql --host db.example.com --source custom --certificate corp-db
noust db tls status
noust db tls refresh                       # what certbot runs after a renewal
noust db tls disable -e postgresql
```

- **Checked first.** The key is the certificate's, it covers the name, it has not expired. Then the
  certificate and key are copied to `/etc/ssl/noust-db/<engine>/` for the engine's account (the source
  stays root's: `/etc/letsencrypt/archive` is not a directory a database should be given).
- **Applied.** PostgreSQL by `ALTER SYSTEM` (`ssl`, `ssl_cert_file`, `ssl_key_file`, a TLS 1.2
  minimum) and a reload; MySQL and MariaDB by `98-noust-tls.cnf` and one restart (asked about).
- **Proved.** The engine is asked what it presents, with a TLS handshake started the way its protocol
  starts it and the name a client would send; the fingerprint must be the certificate's. When it is
  not, the settings are put back.
- **Renewed.** `noust db tls refresh` copies a changed source and makes the engine load it *without a
  restart* (`pg_reload_conf()`, `ALTER INSTANCE RELOAD TLS`, `FLUSH SSL`), then proves what is
  presented again. certbot's deploy hook (the one Noust installs for the web server) runs it after
  every renewal that succeeds. For an uploaded certificate it is the command to run after uploading a
  new one, and `status` says when the copy is behind its source.

The engine does not require TLS of every client because of this; what a public database's account
requires is in [Making a database public](databases-settings.md#making-a-database-public-on-purpose). Redis, Valkey and
MongoDB are not covered, and an instance has its own certificate (`noust db instance tls`).

The API is `GET /api/databases/engines/tls` and `GET /api/databases/engines/{engine}/tls` (what an
engine is given, what it presents now, days left, whether its source was renewed),
`POST .../engines/{engine}/tls/plan` (checks the certificate and says whether the engine restarts) and,
as jobs with sudo mode, `PUT .../engines/{engine}/tls`, `DELETE .../engines/{engine}/tls` and
`POST /api/databases/engines/tls/refresh`. A restart is refused without `confirm`, as
`confirmation_required`, before the job exists. Tested against real PostgreSQL and MariaDB
(`host_tls`).

## Read replicas, over TLS

A replica is another server's database that follows this one's changes and answers read-only queries:
reports and backups can run on it, and it takes over (`promote`) if the primary is lost. It is the
engine's own replication (PostgreSQL's streaming replication; MySQL's and MariaDB's GTID replication)
driven by two commands, one on each server, and it is TLS-only: the primary needs a certificate
clients can verify (see the section above) and the replica connects with the certificate and the name
checked. This section is PostgreSQL's; [MySQL and MariaDB](#mysql-and-mariadb-replicas) follow.

```bash
# On the primary
noust db replica prepare reports --from 203.0.113.7 --host db.example.com --listen '*' \
    --open-firewall --token-file /root/reports.token --plan      # what it would do, and what stops it
noust db replica prepare reports --from 203.0.113.7 --host db.example.com --listen '*' \
    --open-firewall --token-file /root/reports.token             # asks before restarting PostgreSQL

# Carry the file to the replica server (it holds the replication password), then there
noust db replica join --token-file /root/reports.token --plan
noust db replica join --token-file /root/reports.token --replace-data

noust db replica status                    # on either server
noust db replica promote                   # on the replica: it becomes a primary of its own
noust db replica remove reports            # on the primary: the account, the slot and the line go
```

- **What `prepare` makes on the primary.** An account `noust_repl_<name>` with `REPLICATION LOGIN` and a
  generated password (SCRAM), a physical slot `noust_<name>` (so the primary keeps the log the replica
  has not read yet, from the moment the slot exists), and one marked line in `pg_hba.conf`:
  `hostssl replication noust_repl_<name> <address> scram-sha-256`. The line is checked by the server
  (`pg_hba_file_rules`) before it is loaded and put back as it was if the server finds an error in it;
  a connection without TLS finds no line, and the account cannot open any database. If PostgreSQL
  listens on the loopback only, `--listen` says where it should listen too (an address of this server,
  or `*`): that is the one thing that restarts it. `--open-firewall` allows the port from the
  replica's address through ufw or firewalld, and `remove` takes that rule away if Noust made it.
- **TLS is checked before anything is made.** The primary runs, against itself, the handshake the
  replica will run: the certificate must be signed by an authority the replica trusts and valid for
  `--host`. A private authority is given with `--ca-file` and travels in the join code. When the
  certificate would be refused, nothing is created and the message carries openssl's own words.
- **The join code** (`noust-replica:v1:...`) holds the primary's address, the account, its password,
  the slot, the authority and what the primary requires of a standby. It is a secret: it goes to a
  file only root reads (`--token-file`) or is printed once, and `noust db replica token <name>` prints
  it again on the primary. It is never part of a `--plan`, a log line, an audit event or the store.
- **What `join` does to the replica server.** The primary's cluster is copied with `pg_basebackup`
  into a directory beside this server's own, *while this server keeps running*: a primary that cannot
  be reached, whose certificate is not trusted, whose password is wrong or whose disk is too small to
  receive the copy changes nothing. Then PostgreSQL stops once (waited for until its server process is
  gone), the data directory is **renamed to `<data directory>.noust-before-replica`, never deleted**,
  the copy takes its place and PostgreSQL starts as a standby. The databases of this server are
  replaced by the primary's, which is why the plan lists them and `--replace-data` is asked for;
  `--discard-previous` deletes the old directory once the replica streams.
- **When the replica does not start following** (a setting the primary raised since, a version
  mismatch, a primary that went away) the failed attempt is kept at
  `<data directory>.noust-failed-replica`, the previous directory is put back and started, and the
  error carries the engine's journal. A replica is a physical copy, so the major version of
  PostgreSQL must be the same on both servers.
- **What the replica keeps of itself.** The copy brings the primary's `postgresql.auto.conf` (every
  `ALTER SYSTEM` the primary had: its certificate paths, its archiving, its addresses) and, on layouts
  that keep configuration in the data directory, its configuration files. A replica is another
  machine: it keeps its own settings and files, takes the capacity settings a standby must match
  (`max_connections`, `max_worker_processes`, `max_wal_senders`, `max_prepared_transactions`,
  `max_locks_per_transaction`: the larger of its own and the primary's), and follows. The password is in
  a passfile of the `postgres` account (`~postgres/.noust-replica/<name>.pgpass`, 0600) that
  `primary_conninfo` names, never in a setting.
- **Watching it.** `status` on the primary reads each replica from the engine itself: whether it is
  connected, `streaming` or catching up, how far behind in bytes, the TLS version of its connection
  (`pg_stat_ssl`: a connection without TLS is called out), and how much log its slot holds. A replica
  that is gone while its slot stays makes the primary keep the log until its disk fills, so `status`
  warns when a slot holds more than 2 GiB of a replica that is not connected. On the replica it shows
  the receiver, the replay delay and the `sslmode` it follows with.
- **Promote and remove.** `promote` ends the following (`pg_promote`), forgets where it followed and
  deletes the passfile; the old primary keeps the slot until `noust db replica remove <name>` is run
  there, and the message says so. `remove` takes away the account, the slot (ending the sender first),
  the `pg_hba.conf` line, the firewall rule Noust made and the password; `--restore-listen` also
  puts `listen_addresses` back (a restart) when no other replica needs it.
- **What it does not do yet.** PostgreSQL of the server only, not an instance; a replica of a replica
  (cascading) and synchronous replication are not set up; point-in-time recovery must be off on a
  server that joins (the copy has the primary's identity) and is turned on again after promoting; no
  automatic failover: promoting is a decision; the console does not show replicas yet (the
  commands are the interface).

Tested against two real servers on one network (`tests/integration/engines_run.py`, scenario
`replication_postgresql`): the engine refuses a connection without TLS and a wrong password, the
replica copies the cluster, streams, refuses writes and is seen on TLS 1.3 by the primary's
`pg_stat_ssl`; a join with a wrong password or an untrusted certificate changes nothing; a standby
that cannot start puts the previous data directory back; promoting and removing leave both servers
as independent as before.

### MySQL and MariaDB replicas

The same two commands with `--engine mysql` (it covers MariaDB too, whichever is installed); `join`
reads the engine from the code.

```bash
# On the primary
noust db replica prepare reports -e mysql --from 203.0.113.7 --host db.example.com \
    --listen '*' --open-firewall --token-file /root/reports.token --plan
noust db replica prepare reports -e mysql --from 203.0.113.7 --host db.example.com \
    --listen '*' --open-firewall --token-file /root/reports.token

# On the replica (the same flavour, the same version or newer)
noust db replica join --token-file /root/reports.token --plan
noust db replica join --token-file /root/reports.token --replace-data

noust db replica status -e mysql
noust db replica promote -e mysql
noust db replica remove reports -e mysql --restore-listen
```

- **What `prepare` gives the primary.** A file of Noust's, `99-noust-replication.cnf`, in the include
  directory (it sorts after the package's files, so its settings win, and removing it puts them back):
  binary logging with a log expiry where MariaDB had none, GTIDs on where MySQL had them off
  (`gtid_mode`, `enforce_gtid_consistency`), and the address to listen on when `--listen` says so.
  Anything of those the server already has is not written, and a server that needs none of it is not
  restarted. MariaDB listens on one address, so it takes `--listen '*'` (written as `0.0.0.0`); MySQL
  takes a list. Binary logging turned off on purpose in MySQL (`skip-log-bin`) is refused with the fix,
  not overridden.
- **The account.** `noust_repl_<name>` at the replica's address (a network becomes `address/netmask`),
  `REQUIRE SSL`, a generated password of 32 characters (the most MySQL takes for a replication user),
  granted `REPLICATION SLAVE`, `REPLICATION CLIENT`, `RELOAD`, `SELECT`, `SHOW VIEW`, `TRIGGER`,
  `EVENT` and `LOCK TABLES`. **That account can read**, which PostgreSQL's cannot: the first copy of a
  logical replica is a dump taken through it. It cannot write, it connects from one address, and the
  engine itself refuses it without TLS (MariaDB: `Access denied`; MySQL 8: `Authentication requires
  secure connection`). `remove` drops it.
- **TLS** is proved before anything is made, with the handshake of the MySQL protocol and the name the
  replica will use, and verified by the replica both ways: the dump (`--ssl-verify-server-cert`,
  `--ssl-mode=VERIFY_IDENTITY`) and the replication itself (`MASTER_SSL_VERIFY_SERVER_CERT = 1`,
  `SOURCE_SSL_VERIFY_SERVER_CERT = 1`). The authority is kept in
  `/etc/ssl/noust-db/mysql-replica/<name>/ca.pem` for the server's account. The password is in the
  environment of the two clients that talk to the primary and, as it must be, in the server's own
  replication metadata; never in an argument.
- **What `join` does.** In this order, and each step before the first change costs nothing: the
  primary is asked for its databases over TLS (a primary that cannot be reached, trusted or signed in
  to stops here, with the client's words); each user database of this server is dumped with
  `noust db backup` (they are replaced, and these dumps are how they come back); the primary's databases
  are dumped at one GTID position (`mysqldump --single-transaction --routines --triggers --events`,
  `--master-data=1 --gtid` on MariaDB, `--set-gtid-purged=ON` on MySQL) into a private file; this
  server gets its own `server_id` (stable for the name, never the primary's) in `99-noust-replica.cnf`,
  and MySQL a restart for GTIDs; its databases are dropped and the dump loaded; the server is pointed at
  the primary and started; and only when both threads run is it made read-only.
- **When any of that fails after the first change** (a load the server refuses, a primary that goes
  away, a replication that stops with an error) replication is stopped and forgotten, what was loaded is
  dropped, the dumps made first are loaded back, Noust's settings are taken off and the server restarted
  if it was, and the error carries the server's own words. The primary's dump is deleted on every way
  out; `--discard-previous` also deletes the dumps of this server's own databases once it streams.
- **What is not copied.** The accounts of the primary (they live in the `mysql` schema, which is not
  part of a copy of the user databases); the ones created on the primary after the join replicate like
  any statement. And MariaDB's `read_only` does not stop the administrator, as it never has: ordinary
  accounts are refused, `root` is not (MySQL's `super_read_only` refuses everyone).
- **Promote and remove.** `promote` stops reading from the primary, waits for what it already read to
  be applied, stops and forgets the following and lifts read-only. `remove` drops the account (every
  host it exists for), the firewall rule Noust made and the password; `--restore-listen` takes
  `99-noust-replication.cnf` off and restarts the server.
- **What it does not do yet.** MySQL older than 8.0.23 and MariaDB older than 10.4 are refused (they
  lack GTIDs with verified TLS, or `CHANGE REPLICATION SOURCE`); one flavour per pair (a MariaDB
  replica of a MySQL primary is not offered); a replica of a replica; multi-source or semi-synchronous
  replication; no automatic failover.

Tested against two real servers on one network (`tests/integration/engines_run.py`, scenarios
`replication_mariadb` with MariaDB 10.11 and `replication_mysql` with MySQL 8.4 from Oracle's
repository): the engine refuses the account without TLS, with a wrong password, and for anything but
reading; a join with a wrong password or an untrusted certificate changes nothing; a load the server
refuses (`max_allowed_packet`) puts the databases back, restarting MySQL twice; the replica follows
over `Master_SSL_Verify_Server_Cert: Yes`, holds the routines, refuses an ordinary account's write,
receives new accounts and rows, and after `promote` and `remove` both servers are independent again.

The API for all of this is `GET /api/databases/replicas` and `GET .../replicas/{engine}` (`postgresql`
or `mysql`), `POST .../replicas/{engine}/plan`, and, with sudo mode, `POST .../replicas/{engine}` (make
room for a replica; it answers with the join code, which holds the replication password and is
therefore never a job's result), `POST .../replicas/{engine}/{name}/token` (show it again; audited as
`db.replica.token`), `POST .../replicas/join/plan` and `POST .../replicas/join` (a job: the data of the
replica is replaced by a copy of the primary's), `POST .../replicas/{engine}/promote` and
`DELETE .../replicas/{engine}/{name}`. A restart, a replaced data directory and a promotion are refused
without `confirm` (`replace_data` for the join) as `confirmation_required`, before anything is done.
They are the server's: a team member does not reach them. The console does not show replicas yet.
