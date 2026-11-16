# Adopting a running stack

Part of [Docker Compose](compose.md).

A stack that somebody brought up by hand (Shop, in `/opt/shop`, started by its own
`deploy.sh`, served by its own nginx file) cannot be deployed with `noust create`: that would
clone into a directory that holds its data, derive a project name the running containers do not
have (and with it new, empty volumes) and write a second site next to yours. `noust app adopt`
registers it as it is. A site that is not a stack (a unit you wrote, PHP, files) is adopted with
`noust app adopt-site`: see [Adopting sites that already run](adopting.md).

```bash
noust --dry-run app adopt shop.example.com --path /opt/shop   # what it would record; changes nothing
noust app adopt shop.example.com --path /opt/shop             # shows the same, asks, adopts
```

| Option | Meaning |
|---|---|
| `--path DIR` | The directory the stack runs from. Required. A real directory, not a link. |
| `--compose-file F` | The compose file, relative to `--path`. Found otherwise: `docker-compose.prod.yml`, `docker-compose.prod.yaml`, `docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, `compose.yaml`, in that order. |
| `--source URL` | Where updates fetch from. The checkout's `origin` by default. |
| `--branch B` | The branch updates follow. The one checked out by default. |
| `--site NAME` | The file in `sites-available` that serves the domain. Found by the names each file serves otherwise; refused when several enabled files answer on it. |
| `--port N` | The port to register. The compose file's web port otherwise. |
| `--accept-recreate` | Adopt even if starting the stack the way Noust would recreates something (read the output first). |
| `-y`, `--yes` | Adopt without asking. `--json` prints what was adopted. |

Over the API, `POST /api/apps/adopt` (`apps.manage`, sudo mode) takes `domain`, `path`,
`compose_file`, `source`, `branch`, `site`, `port`, `accept_recreate` and `preview`: with
`preview: true` it answers what would be recorded and changes nothing, which is what the console
shows before asking. A rehearsed `up` that would recreate something answers `409` with Compose's
own output in `output`, unless `accept_recreate` is set. In the console it is New application >
Where the code is > **A running stack**.

## What it requires

- `DOMAIN` is not deployed yet, and no unit called `<app>.service` exists.
- `--path` is a git checkout with an `origin` (or `--source` names where to fetch from): an
  update brings an adopted stack up to date by resetting the checkout to the branch. A directory
  that is **not** a checkout, which is what Coolify and Portainer leave, is adopted too, with no
  source and a warning saying so: an update then pulls the images the compose file names and
  recreates the containers, and fetches no code. Naming `--source` for such a directory is
  refused, because Noust does not turn a directory it did not make into a checkout.
- Docker Compose 2.20 or later, for `up --dry-run`.

## What it does

1. **Reads the project from the containers.** The stack runs under the project its containers'
   `com.docker.compose.project` label says, which is not always the directory's name. Noust keeps
   it, and every `docker compose` command and the unit it writes pass it with `-p` from then on.
   When no container of that compose file has ever run, it uses the name Compose would derive.
2. **Proves nothing would change.** It rehearses what the unit and an update run, `docker
   compose -p P -f F up -d --remove-orphans --dry-run --no-build`, and refuses to adopt when
   Compose says it would create, recreate or remove anything, showing that output. That means
   the running containers differ from the compose file (another project, file, profile or
   environment). Bring the stack up the way it is meant to run, or adopt with `--accept-recreate`
   after reading it.
3. **Finds the site.** The file in `sites-available` whose `server_name` lists the domain. If
   its name is not the domain, the name is recorded (`site_name`). The site is yours: it is shown
   as "the operator's, never rewritten".
4. **Creates the unit and enables it, without starting it.** The stack already runs; starting the
   unit would be an `up` nobody asked for.
5. **Registers the application** in place, at its real path (`/opt/shop`, outside the apps
   directory), with its history starting at one entry for the adoption, at the commit that is
   checked out. A stack that publishes no port is registered as a worker, without a site.

It never clones, cleans, rebuilds, restarts or stops anything, and it never edits the directory
or the site. A checkout with changes to tracked files is adopted with a warning: the next update
resets them to the branch, while files git does not track (`.env`, bind-mounted data) are kept.

## Updates afterwards

`noust update` on an adopted stack fetches and resets the checkout to the branch (never
`git clean`), builds, runs the hooks, recreates the containers and judges the result, like any
Compose update. One with no repository skips the fetch and pulls the images instead. The pre-update backup and everything else use the application's recorded path,
not a directory under `/var/www/apps`.

## A worked example: Shop

Shop is a monorepo with a NestJS backend, a Next.js frontend, Postgres and Redis,
`docker-compose.prod.yml`, Prisma migrations and a hand-written nginx site, `shop`, serving
`shop.example.com`. Brought up by its own `deploy.sh` under `/opt/shop`, it adopts, and then gains
migrations as hooks, a copy of Postgres before each update and an update without a cut:

```bash
# 1. adopt it, as it runs
noust --dry-run app adopt shop.example.com --path /opt/shop
noust app adopt shop.example.com --path /opt/shop -y

# 2. in the repository, declare the migration as a hook (see "noust.yaml") and commit it
#    hooks.pre_deploy: ./node_modules/.bin/prisma migrate deploy, in the backend, migrates: true

# 3. let nginx reach the backend and the frontend through the servers files
noust app zero-downtime shop.example.com on
#    refused, with the include line for each upstream block of /etc/nginx/sites-available/shop:
#      include /etc/nginx/noust-upstreams/shop-example-com/backend.servers;
#      include /etc/nginx/noust-upstreams/shop-example-com/frontend.servers;
#    add them in place of the `server 127.0.0.1:PORT;` lines, nginx -t, reload, run it again
noust app zero-downtime shop.example.com on

# 4. from now on
noust update shop.example.com
```

The update dumps Postgres into its pre-update backup, runs the migration in a one-off container
of the new backend image before anything is recreated, recreates the backend and the frontend
behind their relays (the backend first: the frontend depends on it) and, if the backend cannot
start, stops without having touched what serves.

## Deleting an adopted stack

`noust delete shop.example.com` takes the stack's containers down, removes its unit, the images Noust
kept for going back and the records, and leaves what Noust did not create:

- **The directory stays** whatever `--keep-files` says, because it is outside Noust's apps
  directory and was not created by Noust, with everything in it. To remove it with the
  application, name it exactly: `noust delete shop.example.com --remove-adopted-directory /opt/shop`
  (`remove_adopted_directory` in the API). A different path is refused before anything is
  touched.
- **Your site stays,** with its certificate, and so do the servers files it includes. Both are
  reported as kept, with how to remove them, not as a failure.
- **Noust's own servers files** for a stack that was relayed, when no site of yours includes them,
  are removed with it.
