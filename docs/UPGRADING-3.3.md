# Upgrading to 3.3

This guide is written against Noust 3.2.1. A server older than 3.2 goes through
[UPGRADING-3.2.md](UPGRADING-3.2.md) first; the packages do the steps in one upgrade.

The short version: 3.3 changes what Noust sees and says about databases, not what runs. The store
schema does not change (it stays at version 13), no unit, site or application is rewritten, and
the upgrade is the usual package update. A fleet upgrades as before: the nodes with
`noust fleet run noust_update` (a canary first), then the central.

## What you will notice

- **More databases.** The databases of Docker containers appear in the Databases area and in
  `noust db list`, under keys like `postgresql@shop.db`. Seeing them changes nothing: they are not
  tracked, linked or backed up by Noust until you ask (`noust db adopt`, a backup policy, a link).
- **Engines that refused Noust now say so.** If a host engine's listing used to be empty, look
  again: it probably says that the engine does not let Noust in, with its own message. For
  MySQL/MariaDB and Redis, store the account from the console or with
  `noust db config --engine mysql --user root --password`.
- **Detected links.** Applications whose `.env` names a database appear beside it as detected.
  Nothing is linked until you record one ("Record the link" in the console). `noust db adopt` does
  not record them. A recorded detected link carries no account, so rotating that account's
  password does not rewrite the application's `.env`; link it with `noust db link` if Noust
  should.
- **Exposure.** Ports Docker publishes that the `DOCKER-USER` chain already refuses on the public
  interface stop being reported as open. `noust db exposure --json` still lists them, marked
  `"firewalled": true`; its exit status counts only the open ones.
- **Compose stacks started by hand** show as running outside Noust instead of stopped.

## What changes on its own

- The monitor's database metrics sample the containers too, once a minute, through
  `docker exec` (one short read-only query per instance).
- `noust db list` exits 1 when an engine could not be read, so a script notices instead of
  reading an empty list.
- Under the `ens-medium` profile, a database setting that would make an engine listen beyond the
  loopback is refused.

## Rolling back

Downgrading the package to 3.2.1 is enough: the store is the same. Links recorded from detected
uses and settings files written by 3.3 stay (`/etc/postgresql/*/main/conf.d/90-noust.conf`,
`99-noust.cnf`, `/etc/redis/noust.conf`); 3.2.1 ignores the links' origin and the engines keep
reading the files.
