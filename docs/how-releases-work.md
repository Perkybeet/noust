# Releases and the health gate

Part of [Releases](releases.md).

## Two layouts

| Layout | Who gets it | Deploy | Rollback |
|---|---|---|---|
| `releases` | Every application created by 2.0 or later, unless told otherwise | A new directory per deploy, health-gated activation, automatic rollback | Re-point `current` and restart: seconds |
| `inplace` | Every application deployed by 1.x, a monorepo until it is [moved](releases-compose.md#a-monorepo-on-releases), and types that cannot build releases yet | Backup, pull and rebuild over the tree the service is running, restart. A failed health check after the restart is reported, not rolled back, except for Docker Compose and monorepo applications, which [go back automatically](releases-compose.md#docker-compose-and-monorepo-applications). | Rebuild an earlier deployment's commit behind the health gate, or restore a backup ([rolling back to a deployment](releases.md#rolling-back-to-a-deployment)) |

The layout of a new application comes from `--layout` on `noust create` (or `layout` on
`POST /api/apps`), and otherwise from the `deploy.layout` setting, which defaults to
`releases`:

```bash
noust config get deploy.layout
noust config set deploy.layout inplace   # new applications build in place, as in 1.x
```

An existing application always keeps the layout it has. A deploy or an update never changes
it; asking for a different layout on a redeploy is refused with an error. The only way from
`inplace` to `releases` is the explicit migration described [below](releases-migrate.md#moving-an-in-place-application-onto-releases).

Docker Compose applications cannot build releases yet, and a monorepo is created in place. With the
server default they are deployed in place, as before; asking for `--layout releases` explicitly is an
error rather than a silent downgrade. A monorepo moves onto releases with the explicit migration
([below](releases-compose.md#a-monorepo-on-releases)); until then, and for a Compose stack, updates still pass the
health gate and go back to what was serving when they fail it; see
[Docker Compose and monorepo applications](releases-compose.md#docker-compose-and-monorepo-applications).

A container application (`--type container`) has releases of its own kind: each one is an image
named `noust-<app>:<release>`, not a directory. `noust releases list`, `rollback` and `keep` and
the releases API work on them as described here, and going back recreates the container from
the image that is already on the host. See [containers.md](containers.md#releases-every-deployment-is-an-image).

A Docker Compose stack that already ran before Noust (`noust app adopt`) is registered in
place at the directory it runs from, which can be outside the apps directory (`/opt/shop`,
say). Noust never converts, cleans or deletes such a directory on its own; see
[Adopting a running stack](compose-adopt.md#adopting-a-running-stack).

## Layout on disk

```
/var/www/apps/{app}/
  releases/20260925-143012-a1b2c3d/   one complete build; never rebuilt once it is active
  releases/20260924-101500-9f8e7d6/
  current -> releases/20260925-143012-a1b2c3d
  shared/.env                          the environment, mode 0600, outside every release
  shared/<persistent paths>            uploads, storage, data: linked into each release
  repo/                                git cache the releases are exported from
```

`{app}` is the domain with dots replaced by dashes (`shop.example.com` becomes
`shop-example-com`). The systemd unit and the web server site point at `current`, never at a
release directly, so the same unit and site serve whichever release is active.

- A release id is `YYYYMMDD-HHMMSS-<commit>`: the UTC creation time and the first seven
  characters of the commit, or `nogit` for a source that is not a git repository. A second
  release created in the same second gets `-2`, and so on.
- Every link is relative, so the application directory can be moved or restored from a
  backup elsewhere and still resolve.
- `repo/` is an ordinary clone that is fetched and reset on each deploy; the target commit is
  exported into the new release without `.git`. Local directories and archives are copied or
  extracted straight into the release.

## What a deploy does

For an application on `releases`, a deploy (`noust create`, the new-application wizard) and an
update (`noust update`, the console's Update button, `POST /api/jobs/update`, a push webhook)
run the same sequence:

1. **Fetch into a new release.** The source is exported into `releases/<id>/`. The running
   application does not see this directory.
2. **Link what releases share.** `shared/.env` is linked into the release when it exists,
   and each persistent path becomes a link to `shared/<path>`. This happens before the
   install and the build, because both may need the environment or write into a persistent
   directory.
3. **Install dependencies, or reuse them.** When every lockfile (`package-lock.json`,
   `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`, `requirements.txt`, `poetry.lock`, `uv.lock`,
   `Pipfile.lock`) is byte-identical to the active release's, `node_modules`, `.venv` or
   `venv` is copied from the active release with `cp -a --reflink=auto` instead of being
   reinstalled. The deploy log says `Dependencies reused from <release>`. Otherwise the
   install runs normally.
4. **Build** inside the release.
5. **Hand the tree over** to the service user, then run the `pre_deploy` hooks the project or
   the operator declared, if any. Nothing serves the new release yet, so a hook that fails
   ends the deploy here with `current` where it was. See
   [Hooks and schema-aware rollback](releases-hooks.md#hooks-and-schema-aware-rollback).

Steps 3 and 4, and a deployer's own hooks, run in the build sandbox for applications created
from 3.1 on and for those where it was enabled: a transient systemd unit, as the unprivileged
`noust-build` account, able to write the release and the application's own cache in
`/var/cache/noust/build/<app>` and nothing else. The release belongs to `noust-build` while it
builds and is handed to the service user afterwards. Migrations (the release phase, `prisma
migrate deploy`) and the `pre_deploy` and `post_deploy` hooks run as the application, with its
own environment, and are not part of the build; an application that still builds as root runs
them as root, as its migrations always did, until its sandbox is on. See
[Builds in the sandbox](builds-and-accounts.md#builds-in-the-sandbox).
6. **Write the site, the certificate and the unit**, all pointing at `current`. Only a
   deploy (`noust create`, `POST /api/apps`) does this; an update skips it, because the unit
   and the site already point at `current`.
7. **Activate behind the health gate.** See the next two sections. Once the gate passes, the
   `post_deploy` hooks run.
8. **Prune** releases beyond the retention.

When Prisma is detected, its client is generated and `prisma migrate deploy` runs between steps
3 and 4, in the release phase. Since 3.2 a migration that fails ends the update (it was a
warning): see [Hooks and schema-aware rollback](releases-hooks.md#hooks-and-schema-aware-rollback).

`noust update` on an application on `releases` takes no backup first: the release that was
serving stays on disk and is what an automatic or manual rollback returns to.

## The health gate

Activation swaps `current` atomically: a new link is created next to it as
`current.tmp-<random>` and renamed over the old one, so `current` resolves at every instant.
Then the unit is restarted and probed.

| Application | Probe |
|---|---|
| Runs a process (Node, Next.js, Python, ...) | `GET http://127.0.0.1:<port><path>`, one attempt every 2 seconds until the timeout, each allowed 5 seconds or whatever is left of the timeout, if less. The timeout is wall-clock time: an application that accepts connections and never answers is given up on when it runs out, not after its attempts have each waited their 5 seconds. By default the path is `/`, the timeout 30 seconds (at most 15 attempts), and any status below 500 passes, redirects included (they are not followed). |
| Serves files only (static sites, Vite builds without SSR) | The files it serves are there: an `index.html` in the directory the web server serves. |

The same gate judges a deploy, an update, an instant rollback, a migration and a change of
resource limits with `--restart`, so none of them can activate something another would have
refused. `noust diagnose` and the console's Diagnose page probe the same path and judge the
answer by the same statuses.

## Persistent paths and `shared/`

Anything an application writes for itself that must survive a deploy (user uploads, a
SQLite file, `storage/`) has to live in `shared/`, because every deploy starts from a fresh
release directory.

- `.env` is always shared. `noust env`, the console's Environment tab and the backups read
  and write `shared/.env` for an application on `releases`, and `.env` in the application
  directory for one in place.
- Other paths are declared per application: `noust create --persist storage --persist
  public/uploads`, or `persistent_paths` on `POST /api/apps`. Paths are relative to the
  application root and may not be absolute or contain `..`.
- If a release already contains something at a persistent path (usually a file tracked in the
  repository), it is left exactly as it is and reported as a conflict in the deploy log. The
  tracked copy wins; untrack it or drop it from the persistent paths.
- Noust never writes through a symlink found inside a release or under `shared/`. A
  repository is untrusted input, and a tracked link to `/etc` must not become a place Noust
  writes to as root.

## Retention

After a successful activation, releases beyond the newest `N` are deleted, oldest first, where
`N` is the application's retention (5 unless it was changed). The active release is never
deleted, even when a rollback made it older than the newest `N`, and neither is the release
just before it, which is what `noust releases rollback` goes back to. So even a retention of 1
keeps the way back. Rows of failed releases stay listed while they are among the newest `N`
and are forgotten after that.

```bash
noust releases keep shop.example.com       # how many it keeps
noust releases keep shop.example.com 10    # keep ten, from 1 to 50
```

Changing it prunes at once rather than at the next deploy, and says which releases were
removed. Raising it removes nothing, and cannot bring back a release already pruned. Over the
API, `PATCH /api/apps/{domain}/releases/retention` with `{"keep": 10}`, in sudo mode; the
current value is `keep_releases` in `GET /api/apps/{domain}`. An in-place application has no
releases, so it has no retention to set.
