# Settings, limits and exposure

Part of [Databases](databases.md).

## Settings

```bash
noust db settings postgresql                      # current, recommended, restart needed
noust db settings postgresql shared_buffers=2GB work_mem=64MB
noust db settings mysql max_connections=default   # back to the engine's own default
```

Each engine has a closed set of settings. They are written to Noust's own file
(`conf.d/90-noust.conf` for PostgreSQL, `99-noust.cnf` for MySQL and MariaDB,
`/etc/redis/noust.conf` for Redis and Valkey, and MongoDB's YAML), checked with the engine's own
tool, applied, and put back if the engine does not come back, or if the change is interrupted half
way. One change per engine runs at a time. A slow start (a large Redis data set loading, MySQL
recovering after a crash) is waited for up to 15 minutes while the engine says it is still
starting, rather than rolled back.

A change that costs something is refused until it is confirmed (`--yes`, or `confirm` in the API,
whose refusal lists every warning in `warnings`); nothing is written before that:

- listening beyond the loopback, including removing a listen setting (`listen_addresses=default`)
  when what the engine falls back to is not known to be the loopback. Under the `ens-medium`
  profile it is refused outright, and an engine that turns out to listen beyond the loopback once
  it restarts is put back on its previous settings;
- a Redis `maxmemory` below what Redis holds now: with `noeviction` writes start failing, with any
  other policy keys are dropped at once;
- `appendonly no` or `save off` on Redis.

Redis writes a snapshot (`BGSAVE`, waited for) before Noust restarts it, and the restart does not
happen when the snapshot fails. Cache sizes (`shared_buffers`, `innodb_buffer_pool_size`,
`maxmemory`) stop at 90% of the server's memory. A configuration file or directory that is a
symbolic link is refused: Noust writes as root, and the engine's own account owns some of those
directories. On a server with several PostgreSQL clusters running, Noust does not guess which one
to configure and says so; every step targets the one cluster by name.

## Resource limits of an engine on the host

An application has limits behind the health gate and an instance has them from the day it is made
(`noust db instance limits`). The engine the distribution installed on the host can be held to the
same four, so one runaway query does not take the memory of every application beside it:

```bash
noust db limits postgresql                                    # what Noust sets, what systemd holds, what the kernel enforces
noust db limits postgresql --memory 4g --cpus 2               # memory, processors
noust db limits postgresql --swap 0 --tasks 4096              # no swap; processes and threads
noust db limits postgresql --remove cpus                      # take one away
```

They are written to a systemd drop-in of Noust's own beside the engine's unit
(`/etc/systemd/system/<unit>.service.d/90-noust-limits.conf`: `MemoryMax=`, `CPUQuota=`,
`MemorySwapMax=`, `TasksMax=`), never to the distribution's unit file. `daemon-reload` makes systemd
write them into the running service's cgroup, so the engine is **not restarted** and no connection
is dropped. A limit is then proved twice instead of assumed: systemd is asked what it holds for the
unit, and the kernel's cgroup files are read for what it will enforce. A limit systemd accepts and
the kernel ignores (a cgroup v1 host, or memory accounting off) is reported as such. If systemd
does not hold the limit as written (another drop-in sets it later), refuses the reload, or the
engine stops answering, the previous drop-in is put back, systemd reloads it again, and the error
carries the engine's journal verbatim.

A memory limit is a promise the kernel keeps by killing the engine's process when it is exceeded;
for a database that is a crash and a recovery. Set it well above the engine's cache settings
(`shared_buffers`, `innodb_buffer_pool_size`, `maxmemory`); one below what the engine uses now is
refused until it is confirmed (`--yes`, or `confirm` in the API). The least memory accepted is what
the engine needs to start, and nothing above the server's own memory or processors is.

The limits belong to the unit, not to a database: every database of the engine shares them. An
engine in a container has its own (`noust db instance limits`). The console shows the same form
under an engine's settings, and `GET`/`PUT /api/databases/engines/{engine}/limits` is the API
(sudo mode to change, audited as `db.limits.change`).

## Making a database public, on purpose

A database is private: its engine listens on the loopback and `noust db connect-info` prints the SSH
tunnel to reach it. For a desktop client on your own computer there is a deliberate, reversible action:

```bash
noust db public open shop -e postgresql --from 203.0.113.7 --access read-write   # asks first
noust db public list
noust db public show shop -e postgresql --reveal      # the connection, with the password
noust db public close shop -e postgresql              # undoes all of it
```

Opening does four things, in this order, each recorded so it can be undone, and undoes the earlier
ones when a later one fails:

1. **A proxy on the public port you choose** (`--port`, 1024 to 65535; a free one next to the engine's
   is suggested), on the address you choose (`--bind`, every IPv4 address by default): a systemd
   socket and a service running `systemd-socket-proxyd` (`noust-dbexpose-<engine>-<port>`), forwarding
   to the engine's loopback port. The engine is not reconfigured and not restarted, nothing already
   connected notices, and it keeps listening on the loopback only.
2. **An account of its own** for that database, `<database>_remote`, with the access you chose
   (`--access owner|read-write|read-only`, owner by default) on that database alone. Applications'
   accounts and passwords are never the ones given out. The password is shown once and kept as a
   secret (`show --reveal`, sudo mode, shows it again).
3. **TLS required of that account where the engine can**: PostgreSQL refuses it without (one marked
   `hostnossl ... reject` line above every other in `pg_hba.conf`, checked by PostgreSQL before it is
   reloaded and put back if it refuses it), MySQL and MariaDB refuse it without (`REQUIRE SSL` on
   the account). Redis and MongoDB cannot do that for one account, so for them TLS is optional only
   when you say so (`--allow-plaintext`): the password and the data then cross the network in clear.
4. **The firewall**, through the one firewall manager (ufw or firewalld): an allow rule for the port from
   each `--from` address or network, or from anyone when none is given. With no firewall that Noust
   changes, or an inactive one, the warnings say that nothing limits who reaches the port.

Everything it will do, and what you accept by going ahead (anyone who can reach the port can try the
account's password; a Redis account reaches every key), is printed first and asked about (`--yes`;
`confirm` in the API). It is refused whatever you confirm under the `ens-medium` profile, for an engine in a
container Noust did not make (its compose file publishes its ports), for Redis without a password and
MongoDB without authorization (a public port in front of them would be an open door), and for a port that
is taken.

An **instance Noust runs** is public the same way, because Noust publishes it on 127.0.0.1 itself: the
proxy forwards to that port (`noust-dbexpose-postgresql.pg16-<port>`, ordered after the instance's
unit, as the host engine's is) and the instance stays private. What differs is TLS: no
account inside an instance can be made to require it, so PostgreSQL and the other engines whose image
has TLS off are opened only with `--allow-plaintext`, and the plan says so first.

`close` removes what the record says was made and nothing else: the firewall rule only if Noust added it
(an identical rule of yours is left), the units, the TLS line and the account. One step that cannot be
done now (the engine is stopped) leaves the record `closing` and is named; running `close` again
finishes it. The API is `GET /api/databases/public`, `POST .../public/plan`, `POST .../public` (sudo mode),
`GET .../public/{engine}/{database}`, `POST .../reveal` (sudo mode) and `DELETE .../public/{engine}/{database}`
(sudo mode).

## Exposure

`noust db exposure` lists database ports reachable from the network. A port Docker publishes that
the `DOCKER-USER` chain refuses on the public interface is reported as closed by the firewall, not
as open.
