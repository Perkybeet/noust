# From Dokku to Noust

For someone who runs Dokku and wants to know what Noust does differently, what each Dokku command is
called here, what is not done on purpose, and how to move. It is written from Dokku's own
documentation (MIT, https://github.com/dokku/dokku) and from Noust's; where something here is a choice
of design, it says so.

## The difference that explains the rest

Dokku is a **command line and a git remote**: you `git push` to the server, Dokku builds the
repository with Heroku-style buildpacks (or a Dockerfile, Cloud Native Buildpacks, Nixpacks, Railpack),
runs each process type in a Docker container behind an **nginx** it writes the configuration of, and
everything else (domains, certificates, databases, storage) is a command or a plugin. There is no
console, no API and no accounts: whoever has an SSH key Dokku knows is the administrator. Noust runs
applications **as ordinary processes under systemd**, behind the nginx or Apache the server already
has; a build is a release on disk. It has a browser console and an API, and a command line
(`noust`), all clients of the same code. Docker is only there when a project is a Docker Compose
project, a Dockerfile or an image.

What follows from that:

- **A deploy by `git push` exists, as an option.** `noust push setup` makes a repository on the server
  that deploys what is pushed to it ([sources.md](sources.md)), but the usual way is that Noust fetches
  from the host where the code lives, on a push (webhook) or when you ask.
- **A release on disk, not a container per process.** Rollback is flipping a link back, in seconds.
  Dokku keeps the previous image and restarts it.
- **Accounts, roles and an audit trail.** The master token is break-glass; people have accounts with
  roles, two-step verification, passkeys and single sign-on ([security.md](security.md),
  [sso.md](sso.md)).

## Concepts and commands

| Dokku | Noust | Notes |
|---|---|---|
| `apps:create`, `git push dokku main` | `noust create -d app.example.com -s REPOSITORY` | The stack is detected from the repository's files ([frameworks.md](frameworks.md)). |
| Buildpacks (Herokuish, Cloud Native, Nixpacks, Railpack) | Detection and the commands of the type | A `Procfile` is read: its process types run as workers beside the web process ([build.md](build.md)). |
| Builder: Dockerfile | `--type container` | One image per release. |
| Deploy from an image (`git:from-image`) | A container application from a registry image | `noust create -d app.example.com --image IMAGE`. |
| `config:set`, `config:export` | `noust env`, an application's variables | Build-only variables and secret managers ([variables.md](variables.md)). |
| `domains:add`, `domains:set` | `noust domain add` | Aliases and redirects ([domains.md](domains.md)). |
| `letsencrypt:enable`, `certs:add` | A certificate from Let's Encrypt, or an uploaded one | Issued with the first deploy; renewed by a timer ([certificates.md](certificates.md)). |
| `ports:set`, `proxy:ports-set` | The application's port, and `noust stream` for other TCP and UDP ports | [network.md](network.md). |
| `storage:mount` | Persistent paths (`--persist`), and `noust app mount` | |
| `ps:scale web=2` | Not applicable | One process per application on a server. |
| `ps:rebuild`, `ps:restart` | `noust update`, `noust restart` | |
| `checks` and `app.json` healthchecks | The health gate | Runs on every activation and also on a rollback; configurable per application (`noust app health`). |
| `resource:limit` | `noust app limits` | systemd resource control. |
| `cron` (`app.json`) | `noust cron create` | systemd timers, with history. |
| `postgres:create`, `mysql:create`, `redis:create`, ... (plugins) | `noust db` | `noust db provision DOMAIN -e postgresql` creates a database and links it; `noust db instance` runs another version ([databases.md](databases.md)). |
| `postgres:export`, `postgres:backup-schedule` | `noust db backup`, `noust db backup-schedule` | With destinations and verification ([disaster-recovery.md](disaster-recovery.md)). |
| `nginx:set`, custom `nginx.conf.sigil` | The site, edited | As text, as a structure or as a diagram ([domains.md](domains.md#operator-sites)); Noust never rewrites a site it did not write. |
| `logs`, `enter`, `run` | `noust logs`, the application's terminal, `noust app exec` | [terminal.md](terminal.md). |
| `review apps` (a plugin) | Previews | `noust preview enable`; one per pull request. |
| `ssh-keys:add` | Deploy keys, SSH keys under **Server > Security** | |

## What Noust does not do, on purpose

- **No one-key-is-administrator.** Whoever has an SSH key Dokku knows can do anything; Noust has roles.
- **No process-type scaling by count.** `ps:scale` has no counterpart: an application runs once.
- **No plugin system for engines.** A database engine is one of Noust's (PostgreSQL, MySQL, MariaDB,
  Redis, Valkey, MongoDB on the host; KeyDB, Dragonfly, ClickHouse and other versions as instances),
  with the same backups and credentials for all.
- **No telemetry or error reporting to third parties.**

## Moving across

Dokku has no API, so Noust asks it with its own commands, read-only: on the Dokku server itself
(install Noust beside it, or on the machine you are migrating to), or from another machine over SSH as
the `dokku` user. Nothing is written to Dokku.

```bash
noust import --from dokku                                  # on the Dokku server: the applications
noust import --from dokku --app shop                       # one, as Noust would run it
noust import --from dokku --ssh dokku@dokku.example.com --app shop    # from another machine
```

Over SSH the user is always `dokku`, with a key Dokku accepts (`dokku ssh-keys:add`) and a host key
already in your `known_hosts` (`ssh dokku@dokku.example.com` once): a host that is unknown or whose key
changed stops the import, it is never accepted silently. The importer runs `dokku apps:list`,
`config:export`, the `*:report` commands with the flag that prints one value, `cron:list` and
`ps:scale`, and nothing else.

What carries over: the environment (without what Dokku sets for itself: `DOKKU_*`, `GIT_REV`; the
values that look like a credential are asked for again, so a `DATABASE_URL` that names a Dokku service
is not carried to a place it cannot reach), the domains, the port the container listens on (from the
port map), the memory and processor limits, the persistent storage (as `--persist` paths where it is
under `/app`, with the command that copies the data and the one that puts it back), the cron tasks of
`app.json`, and the databases of Dokku's service plugins the application is linked to, found by the
host name the plugin gives them (`dokku-postgres-SERVICE`), with the plugin's own export as the dump
(`dokku postgres:export SERVICE > FILE`) and the `noust db import` that loads it. What is only said,
with what to do instead: the builder (Noust detects the stack, and runs a Dockerfile as a container
application), scaling, and the code: Dokku keeps it in a repository on its own server, so push the same
repository to a host Noust can fetch from and create the application with `--source`.

1. **Install Noust** ([cloud-init.md](cloud-init.md), [PLATFORMS.md](PLATFORMS.md)). On the same server
   Dokku's nginx and Noust's both want ports 80 and 443: `noust setup doctor` says who holds them.
   Move the applications one at a time to a second server, or stop Dokku's nginx when the last one is
   moved.
2. **Read it**: `noust import --from dokku --app shop`.
3. **Deploy it**: `noust create -d shop.example.com -s https://github.com/you/shop.git --env-file
   shop.env --persist storage`.
4. **Move the data** with the commands the importer printed.
5. **Move DNS**, then `dokku apps:destroy shop` when you are sure.

## Moving the other way

`noust app export APP -o app.json` writes everything that defines an application as JSON, and
`noust app import` deploys it on another Noust ([leaving.md](leaving.md)).
