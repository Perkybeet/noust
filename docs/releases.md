# Releases

Noust builds every deploy of a new application in its own directory, switches to it
atomically, keeps it only if it answers, and can go back to any release still on disk in
seconds. This page describes that layout, how a deploy moves through it, and how an
application deployed by WASM 1.x is moved onto it.

## Two layouts

| Layout | Who gets it | Deploy | Rollback |
|---|---|---|---|
| `releases` | Every application created by WASM 2.0 or later, unless told otherwise | A new directory per deploy, health-gated activation, automatic rollback | Re-point `current` and restart: seconds |
| `inplace` | Every application deployed by WASM 1.x, and types that cannot build releases yet | Backup, pull and rebuild over the tree the service is running, restart. A failed health check after the restart is reported, not rolled back, except for Docker Compose and monorepo applications, which [go back automatically](#docker-compose-and-monorepo-applications). | Rebuild an earlier deployment's commit behind the health gate, or restore a backup ([rolling back to a deployment](#rolling-back-to-a-deployment)) |

The layout of a new application comes from `--layout` on `noust create` (or `layout` on
`POST /api/apps`), and otherwise from the `deploy.layout` setting, which defaults to
`releases`:

```bash
noust config get deploy.layout
noust config set deploy.layout inplace   # new applications build in place, as in WASM 1.x
```

An existing application always keeps the layout it has. A deploy or an update never changes
it; asking for a different layout on a redeploy is refused with an error. The only way from
`inplace` to `releases` is the explicit migration described [below](#moving-an-in-place-application-onto-releases).

Monorepo and Docker Compose applications cannot build releases yet. With the server default
they are deployed in place, as before; asking for `--layout releases` explicitly is an error
rather than a silent downgrade. Their updates still pass the health gate and go back to what
was serving when they fail it; see [Docker Compose and monorepo applications](#docker-compose-and-monorepo-applications).

A Docker Compose stack that already ran before Noust (`noust app adopt`) is registered in
place at the directory it runs from, which can be outside the apps directory (`/opt/proggest`,
say). Noust never converts, cleans or deletes such a directory on its own; see
[Adopting a running stack](compose.md#adopting-a-running-stack).

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
   [Hooks and schema-aware rollback](#hooks-and-schema-aware-rollback).

Steps 3 and 4, and a deployer's own hooks, run in the build sandbox for applications created
from 3.1 on and for those where it was enabled: a transient systemd unit, as the unprivileged
`noust-build` account, able to write the release and the application's own cache in
`/var/cache/noust/build/<app>` and nothing else. The release belongs to `noust-build` while it
builds and is handed to the service user afterwards. Migrations (the release phase, `prisma
migrate deploy`) and the `pre_deploy` and `post_deploy` hooks run as the application, with its
own environment, and are not part of the build; an application that still builds as root runs
them as root, as its migrations always did, until its sandbox is on. See
[Builds in the sandbox](#builds-in-the-sandbox).
6. **Write the site, the certificate and the unit**, all pointing at `current`. Only a
   deploy (`noust create`, `POST /api/apps`) does this; an update skips it, because the unit
   and the site already point at `current`.
7. **Activate behind the health gate.** See the next two sections. Once the gate passes, the
   `post_deploy` hooks run.
8. **Prune** releases beyond the retention.

When Prisma is detected, its client is generated and `prisma migrate deploy` runs between steps
3 and 4, in the release phase. Since 3.2 a migration that fails ends the update (it was a
warning): see [Hooks and schema-aware rollback](#hooks-and-schema-aware-rollback).

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

### Configuring the health check

An application that has a health endpoint, needs longer to start, or must not count a 404 as
up can say so. Three settings, each with the default above when unset:

| Setting | Accepts | Default |
|---|---|---|
| Path | A path on the application: begins with a single `/`, printable ASCII, no spaces. A query string is fine (`/health?deep=1`); a scheme or a host is refused, because the probe always asks the application itself on `127.0.0.1`. | `/` |
| Expected statuses | Statuses and inclusive ranges from 100 to 599, separated by commas: `200`, `200-399`, `200,204`, `200-299,301`. Only these pass; a redirect is not followed, so a `301` passes only if it is listed. | any status below 500 |
| Timeout | Seconds from 5 to 600 the release gets to answer, wall-clock: one probe every 2 seconds until they run out. | 30 |

```bash
noust app health shop.example.com                                  # the current settings, defaults marked
noust app health shop.example.com --path /healthz --expect 200-299
noust app health shop.example.com --timeout 120                    # options not named keep their value
noust app health shop.example.com --reset                          # back to every default
```

Over the API, `PATCH /api/apps/{domain}/health` sets the three together (a field left out or
null goes back to its default) and needs sudo mode. See [api.md](api.md). The values are
validated where they are stored, so the CLI, the console and the API refuse the same input
with the same message. Nothing restarts: the next activation uses the new settings. A redeploy
keeps them. A static site has no settings to change: its check is its files.

## Automatic rollback

When a new release fails the gate:

1. The release is recorded as `failed` and its directory is removed.
2. `current` is pointed back at the release that was serving, and the unit restarted and
   probed again.
3. The deployment fails with the evidence verbatim: every failed probe (identical
   consecutive failures folded into one line) and the last 40 lines of the unit's journal.

The error says which release is active again and whether it answered. A failed release stays
in `noust releases list` for a while, marked as not on disk, and cannot be activated. If the
very first release of an application fails, there is nothing to go back to and the deploy
fails; a first deploy that fails undoes the steps it ran.

## Instant rollback

```bash
noust releases list shop.example.com                 # newest first; the active one has an asterisk
noust releases rollback shop.example.com             # the release created just before the active one
noust releases rollback shop.example.com 20260924-101500-9f8e7d6
```

Nothing is rebuilt: `current` is re-pointed and the unit restarted, behind the same health
gate. If the target does not answer, the release that was serving is put back and the command
fails with the probe's and the journal's output. Rolling forward works the same way: name a
newer release. Going back past a deployment that changed the database schema is refused until
you confirm it: see [Hooks and schema-aware rollback](#hooks-and-schema-aware-rollback).

In the console, the Deployments tab of an application lists its releases; activating one is
the same operation. Over the API:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.example.com/api/apps/shop.example.com/releases/20260924-101500-9f8e7d6/activate
```

This needs the `apps.deploy` permission (an `operator` or an `admin`, or a token with the
`deploy` scope), the same as queueing an update. See [api.md](api.md).

Release statuses, as `noust releases list --json` and `GET /api/apps/{domain}/releases`
report them: `active`, `superseded`, `rolled_back`, `failed`, `built`. `active` is whatever
`current` points at, regardless of what the store row says.

### Releases and backups

`noust releases rollback` and `noust rollback` are different tools:

- `noust releases rollback` switches between builds that are already on disk. It is instant,
  and it does not touch `shared/`: the environment and uploaded files are the same before and
  after.
- `noust rollback` restores a backup archive, taking a safety backup of the current state
  first. It is the recovery tool for data and for in-place applications. See `noust backup`.

## Rebuilding a deployment's commit

Update pulls the head of the branch. To deploy exactly the commit an earlier deployment was
built from, name it:

```bash
noust update shop.example.com --commit 9f8e7d6     # full or abbreviated, at least 4 characters
```

In the console, "Redeploy" on a deployment's page does the same:
`POST /api/apps/{domain}/deployments/{id}/rebuild` queues the update job with that
deployment's commit and answers `202` with the job. It needs `apps.deploy`, like an
update, and answers `409 no_commit` for a deployment whose source is not git.

The id must name exactly one commit. It is looked up in the clone first; one the clone does
not have is fetched (a full id by name, an abbreviation by fetching every branch, with the
rest of the history when the clone is shallow). An id that names more than one commit asks
for more characters; one that names none is an error. `--commit` does not combine with
`--source` or `--branch`.

- **On releases**, when a release built from that commit is still on disk, is not the
  active one and did not fail its health gate when it was last activated, it is activated
  behind the health gate, as `noust releases rollback` would: nothing is built. Otherwise, including when the commit is the one that is live, the commit
  is exported from `repo/` into a new release and built like any update. Rebuilding the live
  commit is how a changed environment or a broken dependency install gets a clean build.
- **In place**, the pre-update backup is taken, the checkout is detached at the commit
  (`git checkout --force --detach`: tracked files are rewritten, untracked ones such as
  uploads stay), and the tree is rebuilt and restarted. The branch the checkout was on is
  remembered in the repository's own configuration (`wasm.branch`), so the next
  `noust update` without `--commit` checks that branch out again and pulls it: the
  application follows its branch again. The deployment history records that branch, not
  `HEAD`.

## Which branch an update builds

An update builds one branch, decided the same way by `noust update`, the console's Update, the
nothing-new check and the deploy webhook's filter, so a push is accepted exactly when the update
it queues would build the pushed branch:

1. **A branch named for this update**: `noust update DOMAIN --branch X`. When the update
   succeeds, `X` becomes the application's pinned branch.
2. **Otherwise, the pinned branch.** `noust app branch DOMAIN BRANCH` pins one (the branch must
   exist on the remote; nothing is rebuilt until the next update), `noust app branch DOMAIN`
   shows it, and `--unpin` removes it. In the console: application > Settings > General.
   `PATCH /api/apps/{domain}/branch` over the API.
3. **Otherwise, as before 3.1**: in place, the branch the checkout is on (the update pulls it
   and records it); on releases, the branch recorded for the application. The repository cache
   of an application on releases is never learned from: in 3.0 a one-off `update --branch
   hotfix` left the cache on `hotfix`, and the next plain or webhook update switched production
   to it.

With a branch pinned, the webhook ignores pushes to any other branch and records them as
ignored (`noust app webhook deliveries DOMAIN`). With no branch to go by at all, any push
deploys; `noust app webhook show` warns about it.

An application can follow **tags** instead of a branch (`noust create --follow-tags 'v*'`, or
`noust app follow-tags DOMAIN 'v*'`; `--off` returns to a branch). It deploys the newest tag
that matches the glob, in version order, and never an older tag than the one deployed;
`noust update DOMAIN` with no tag deploys that newest tag, and `noust update DOMAIN --tag v1.2.3`
a named one. See [Deploying by tag](compose.md#deploying-by-tag) and the webhook's handling of
releases and tag pushes in [api.md](api.md#deploy-webhooks).

## Nothing new to deploy

Before an update from the console or from `noust update`, Noust asks the remote for the head
of the branch the application follows (`git ls-remote`: nothing is downloaded) and compares
it with the commit that is live: the active release's on releases, and in place the commit
of the last successful deployment (the checkout's own commit only when the history recorded
none). Not the checkout's: after an update whose build failed, the checkout is on the new
commit while the old build still serves, and the new commit is exactly what is still to be
deployed.

The remote gets 15 seconds to answer. One application is asked about once at a time: a
second update asked for while the first question is out waits for its answer, and an
answer is reused for 10 seconds (an update, an activation or a rollback forgets it at once).

When they are the same:

- The console's update (`POST /api/jobs/update`) answers `409` with `error: "nothing_new"`,
  `detail` "No new commits on main since 9f8e7d6, which is live" and a hint: rebuilding the
  same commit still makes sense when the environment or the dependencies changed, or the
  last build broke. Sending `{"domain": ..., "force": true}` rebuilds anyway.
- `noust update` says the same and asks whether to rebuild anyway. `-y` or `--force` skips
  the question. Without a terminal (a script, cron) there is nobody to ask: it rebuilds and
  says so.

The check is skipped, and the update goes ahead, for a source that is not git (a local
directory or an archive), for an update with `--commit` or `--source`, and when the remote
cannot be asked (the reason is printed as a warning; the update itself then reports the
failure in full). A push webhook never checks: the push is itself the news.

## Rolling back to a deployment

Every deployment in the history says whether it can be gone back to:
`GET /api/deployments` and `GET /api/deployments/{id}` carry `rollback_available` and, when it
is false, `rollback_unavailable_reason`. `POST /api/apps/{domain}/deployments/{id}/rollback`
queues it as a job (`202`), with `apps.deploy`; a deployment that cannot be gone back to
answers `409 rollback_unavailable` with the reason, and one that would pass a deployment that
changed the database schema answers `409 schema_changed` until the body says
`{"schema_changed_ok": true}` (see
[Hooks and schema-aware rollback](#hooks-and-schema-aware-rollback)).

- **On releases**, going back to a deployment activates the release it built, behind the
  health gate. It must still be on disk, not already active, and not one that failed its
  health gate the last time it was activated; a pruned or failed one can be rebuilt from its
  commit instead.
- **In place, in a git checkout** (every type, monorepos and Docker Compose included), going
  back rebuilds the deployment's commit where the application runs: exactly
  `noust update <domain> --commit <commit>`, recorded as a rollback. The pre-update backup is
  taken, the checkout is detached at the commit (tracked files are rewritten; `.git`, uploads,
  SQLite files, the `.env` and anything else untracked stay as they are), and the tree is
  rebuilt. The restart passes the health gate: one that does not answer fails the rollback,
  its row in the history is `failed` with the probes and the journal, and the deployment that
  was serving stays the live one in the history. A monorepo restarts and probes every
  workspace and a stack recreates its containers, and both go back to what served when the
  result does not pass, as their updates do (see
  [Docker Compose and monorepo applications](#docker-compose-and-monorepo-applications)). A
  build that fails restarts nothing: the previous build keeps serving. When it succeeds, the
  deployment it replaced is marked `rolled_back`. Every finished, successful deployment with a
  commit can be gone back to except the one that is live (the newest finished deployment, when
  it succeeded; after a failed update, the last good one can be rebuilt to repair the tree).
- **In place, without history** (a tree that is not a git checkout, or a deployment that
  recorded no commit), going back restores the deployment's snapshot. When an in-place update
  takes its pre-update backup, that backup holds exactly what the previous deployment produced,
  so it is recorded as that deployment's `snapshot_backup`. It is only recorded when it is
  true: the most recent finished deployment must have succeeded, and both the backup and the
  deployment must know the commit, and agree on it. An update that never finished leaves its
  row `running` and the tree half-changed, and only the commit tells that apart; so a tree
  without history gets no new snapshots, and this path serves the snapshots linked before 2.1.
  Going back restores the snapshot with `noust rollback`'s machinery, held to what a deploy is
  held to: a safety backup of the current state first, the restore (keeping `.git` if the tree
  has one, and the deployed `.env` unless `restore_env` is asked for), a rebuild that must
  succeed, the tree handed back to the service user, and the health gate. A failed rebuild or
  a gate that does not pass fails the rollback, and the error names the safety backup that
  holds what served before it (`noust rollback <domain> <backup>`). A restore puts back the
  whole tree as it was: files the application wrote into it since (uploads, a SQLite file)
  go back in time too, which is why a git checkout is never gone back to this way. Monorepo
  and Docker Compose applications without history are not offered it: a restored tree does not
  bring back their workspaces' builds or their images. A snapshot whose backup was rotated
  away is no longer offered either.

## Hooks and schema-aware rollback

A project often has something to run around a deploy that Noust cannot know: apply the database
migrations before the new code serves, purge a cache after it does. And going back to older code
is safe for the code and not for the database: Noust puts code back, never a database. 3.2 adds
hooks for the first and a confirmation for the second.

### Deploy hooks

Hooks are declared in a `noust.yaml` (or `.noust.yaml`) at the root of the repository, or by the
operator for one application, who then replaces the repository's wholesale (they are never
merged):

```yaml
hooks:
  pre_deploy:
    - run: ./node_modules/.bin/prisma migrate deploy
      timeout: 600        # seconds, 1 to 3600 (default 600)
      migrates: true      # it changes the database's schema
  post_deploy:
    - run: ./scripts/purge-cache.sh
```

```bash
noust app hooks show shop.example.com                 # which hooks the next deploy runs, and where they come from
noust app hooks set shop.example.com --file hooks.yaml
noust app hooks clear shop.example.com                # the repository's noust.yaml applies again
```

The full schema (`service`, `workdir`, `backup.databases`), the validation rules and the
operator's hooks are in [compose.md](compose.md#noustyaml) and
[compose.md](compose.md#operator-hooks). What every type shares:

- **`pre_deploy` runs after the build and before anything serves the new version.** On
  releases that is after the tree is handed to the service user and before `current` moves; in
  place, before the restart; a monorepo runs them at its root, before any unit restarts. Hooks
  run in order, each as an argv (never through a shell), in the release phase, the one
  its migrations use: as the account the application runs as, with its `.env`, in the tree
  being deployed, in a transient unit when the application builds in the sandbox, and as root
  when it still builds as root, like its build. The first that exits non-zero or runs out of time ends the
  deployment: the release is abandoned like a failed build, `current` and the running unit are
  untouched, and the error carries the hook's output verbatim.
- **`post_deploy` runs once the new version passed its health gate.** A failure does not undo
  what already serves: the deployment is recorded as successful with warnings (`warnings` on
  the deployment, a `deploy_hook_failed` notification) and the output is in its log.
- **Static sites have no hooks**, nor does any site that is only files the web server serves
  (a Vite build without a server). Nothing of them runs, so declaring some is refused with the
  reason. A Docker Compose stack runs each hook in a one-off container of its new image; see
  [compose.md](compose.md#noustyaml).

### Prisma

A project that uses Prisma and declares no hooks keeps getting `prisma migrate deploy` after the
install, as before. Two things changed in 3.2:

- **A failing migration now ends the update.** Until 3.1 its failure was a warning in the log
  and the new code went on to serve against the schema it was not written for. It is treated
  as a failed `pre_deploy` hook: nothing is switched over and the error carries Prisma's own
  output.
- **Declared hooks replace it.** When `noust.yaml` or the operator declares any hook, the
  automatic migration does not run and the log says so. What the project states explicitly wins.

### What the history records

Each deployment row keeps the hooks it ran, including Prisma's automatic migration (marked
`automatic: prisma`): the command, its exit code, whether it timed out, how long it took and
the end of its output (the whole output is in the deployment's log). The console shows them on
the deployment's page and the CLI in the deploy's output.

A deployment is marked **`schema_changed`** when a hook declared `migrates: true` succeeded, or
Prisma applied at least one migration. A `migrates` hook that printed, in its tool's own
words, that it had nothing to apply (Prisma, Django, Laravel, Knex, Sequelize, TypeORM,
Doctrine, Flyway and golang-migrate are recognised) did not change the schema; one whose
output says neither counts as a change, because a schema that may have changed is treated as
one that did. Prisma's automatic migration that fails after applying some is marked too. A
declared hook that fails is not (only the ones that succeeded before it are), so read the
output of a failed migration before going back.

### Going back past a schema change

Going back past one or more deployments marked `schema_changed` is refused, naming them, until
the operator confirms that the older code works with the schema as it is now. Every way back
passes the same guard:

| Way back | Confirm with |
|---|---|
| `noust rollback DOMAIN [BACKUP_ID]` (restore a backup) | `--schema-changed-ok` |
| `noust releases rollback DOMAIN [RELEASE]` and the console's **Activate** | `--schema-changed-ok`; `?schema_changed_ok=true` on `POST /api/apps/{domain}/releases/{release_id}/activate` |
| `noust update DOMAIN --commit SHA`, when the commit is older than a deployment that changed the schema | `--schema-changed-ok` |
| `POST /api/apps/{domain}/deployments/{id}/rollback` | `{"schema_changed_ok": true}` in the body |
| `noust backup restore BACKUP_ID`, files only | `--schema-changed-ok`; `schema_changed_ok` in the body of `POST /api/backups/{backup_id}/restore` |
| `POST /api/jobs/rollback` | `schema_changed_ok` in the body |

The refusal says "Going back to release 20260924-101500-9f8e7d6 of shop.example.com passes
deployment 41, which changed the database schema", and then how to go on: restore the database
from the backup taken before that deployment (`noust backup list shop.example.com`, then
`noust backup restore BACKUP_ID`), or confirm that the older code works with the new schema.

Over the API the refusal is `409` with `error: "schema_changed"` and the deployment ids in
`deployments`; nothing was touched. In the console, **Roll back to this**, **Activate** and the
backups' **Restore** answer it with a dialog that names the deployments and the migrations each
ran, and shows the backup taken before the first of them with the command that restores it.

- **A restore that puts the database back is not asked.** A backup that carries a Compose
  stack's database dumps restores them with its files (see
  [compose.md](compose.md#rolling-back-past-a-migration)); `--databases-only` puts back just
  those. A restore into another domain (`--target-domain`) is a copy and is not asked either.
- **The automatic way back is not refused.** When the health gate fails, the previous release
  (or the previous containers, or the in-place tree) is put back whatever the attempt did,
  because the alternative is an application that stays down. If the attempt had changed the
  schema, the first sentence of the error says so: "The database schema was changed by this
  attempt and was not undone." The row in the history is marked too.
- **Before pressing the button.** `GET /api/deployments` and `GET /api/deployments/{id}` carry
  `schema_changed` (this deployment changed it) and `schema_changed_between` (the later
  deployments of the application that did, which going back to this one passes), next to
  `rollback_available`. The console and the fleet show them before the click.

For a stack, an update copies its databases first (on by default, and it stops the update when
the copy fails), which is the backup the refusal proposes:
[Docker Compose](#docker-compose) below and [compose.md](compose.md#stack-database-backups-and-restore).

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

## Moving an in-place application onto releases

An application deployed by WASM 1.x keeps running and updating in place after the upgrade, exactly
as before. Moving it onto releases is an explicit operation, one application at a time:

```bash
noust --dry-run app migrate shop.example.com    # the plan, and a rehearsal that changes nothing
noust app migrate shop.example.com              # asks for confirmation; -y to skip it
noust app migrate shop.example.com --persist storage --persist public/uploads
```

`--dry-run` is a global option and goes before the command. The console offers the same
operation from the application's Settings tab; the API has `GET /api/apps/{domain}/migrate/plan`
(read only) and `POST /api/apps/{domain}/migrate` (needs sudo mode), which queues the migration
as a job and answers `202` with it.

What the migration does:

1. **The unit is stopped first**, so nothing writes into the tree while it moves. The plan
   states this downtime; it lasts until the health gate passes, usually seconds.
2. **The live tree becomes the first release.** It is moved, not copied: every change is a
   rename inside the application directory, or the creation of a directory or a link.
3. **The environment moves to `shared/`.** `.env` and Noust's inventory of it (`.wasm`) are
   moved to `shared/` and linked into the release.
4. **What the application wrote for itself moves to `shared/`.** In a git checkout that is
   every untracked directory, ignored or not (`git status --ignored`), minus build output
   (`node_modules`, `.next`, `dist`, `build`, `.venv`, `__pycache__` and similar). Without
   git, Noust cannot tell uploads from code: it keeps whichever of `uploads`,
   `public/uploads`, `storage` and `data` exist, and warns you to name the rest. `--persist`
   replaces detection entirely. SQLite databases are found by their file header wherever
   they are (outside build output) and always move to `shared/`, with their `-wal`, `-shm`
   and `-journal` files beside them; a `--persist` list that leaves one out is refused.
5. **The unit and the site are rewritten to run from `current`**, when they name the
   application directory. A proxied site only names a port and is left alone.
6. **The file count is verified.** Regular files and their bytes are counted before and after,
   `shared/` included. Any difference fails the migration.
7. **The application goes through the health gate.** If it does not answer, every rename is
   reversed, the unit and site are put back byte for byte, and the application is restarted
   on the tree it had. Every undo step is attempted even when an earlier one fails; anything
   that could not be put back is listed in the error with where it is now.

Read the plan before confirming. Two warnings in it matter:

- **Untracked files outside a persistent path** stay in the first release only. The next
  deploy will not have them. Name the directory that holds them with `--persist`.
- **A tree that is not a git checkout** gets only the usual upload directories. Anything else
  the application writes must be named with `--persist`.

The migration refuses an application that is already on releases, one whose type cannot build
releases (monorepo, Docker Compose), and one whose directory is missing. After it, the first
`noust update` builds a second release from the recorded source through `repo/`.

## Docker Compose and monorepo applications

Neither type builds releases, so an update rebuilds it in place. Since 2.1 the update records
what is serving before it touches anything, judges the result with the same health check as
a release (the path, statuses and timeout [configured](#configuring-the-health-check) for the
application), and puts back what was serving when the result does not pass. The update then
fails with the evidence verbatim, and its row in the deployment history is `failed`, recording
the commit that failed.

What "what was serving" means is the commit the tree was on before the update pulled, plus,
for a stack, the image each running container was created from.

### Docker Compose

Before the steps below, the update takes its usual backup, and for a stack that backup carries a
dump of each database the stack runs (Postgres, MySQL, MariaDB and MongoDB, found in the
compose file). The copy is on by default and **stops the update when it cannot be made**,
because without it a migration cannot be undone; `noust app backup-before-update DOMAIN off`
(or `backup.databases: off` in `noust.yaml`) turns it off. See
[compose.md](compose.md#stack-database-backups-and-restore).

1. **Record what serves.** `docker compose ps -q` lists the running containers and
   `docker inspect` reads, for each, the image it runs, the image name it was created from and
   its Compose service and project. Each image is also tagged
   `<project>-<service>:wasm-previous`, because the build is about to move the service's own
   name to a new image, and an image with no name is what `docker image prune` deletes. Each
   update moves that tag, so one extra image per service is kept on disk.
2. **Build.** A failed build recreates nothing: the containers still run the old images. The
   tree is checked out at the previous commit and every image name is pointed back at the image
   that served, so the next start of the unit (after a reboot, say) does not bring up the half of
   the stack that did build.
3. **Run the `pre_deploy` hooks**, if any, each in a one-off container of the image just
   built (`docker compose run --rm --no-deps`), with the service's own environment and network
   and before anything is recreated. A hook that fails is handled like a failed build: nothing
   was recreated, the tree and the image names are put back, and the error carries the hook's
   output. See [compose.md](compose.md#noustyaml).
4. **Recreate** with `docker compose up -d --remove-orphans`. With zero-downtime on, each web
   service the site reaches through Noust's servers file is recreated first while a relay
   container of its new image serves it, one at a time in `depends_on` order, and the rest of
   the stack follows with `up -d`; see
   [compose.md](compose.md#zero-downtime-updates-the-relay).
5. **Judge.** A stack with a web port is probed like any other application, on the port the
   site proxies to. Every stack then has its containers read with `docker compose ps -a`: a
   container that keeps restarting, is dead, exited with a non-zero code or reports itself
   `unhealthy` fails the update. A container that ran once and exited 0 (a migration, a seed)
   is fine. A headless stack (no ports) is judged by its containers alone.
6. **Run the `post_deploy` hooks**, if any, once the stack passed. A failure is a warning on
   the deployment, not a failure of the update.
7. **Go back** when it does not pass: the last 40 lines of the containers' output are read
   first (`docker compose logs --tail 40`), then the tree is checked out at the previous commit,
   every image name is pointed back at the image that served (`docker image tag`), and
   `docker compose up -d --no-build --remove-orphans` recreates the containers from them, in
   the project they belonged to. Every step is attempted even when an earlier one fails, and
   each one that failed is named in the error with Docker's or git's own output. When all of
   them succeeded, the stack is judged again, and the error says whether it answers.

Volumes are never touched. Nothing on the way runs `docker compose down`, `-v`, `--volumes`,
`--renew-anon-volumes` or any `docker volume` command: recreating a container reattaches its
named volumes by name and carries its anonymous ones over. That also means a database
migration the failed version ran inside its container is not undone; the previous version
runs against the data as the new one left it. That is what the database copy taken before the
update is for: when a failure error opens with "The database schema was changed by this attempt
and was not undone", put the data back with `noust backup restore BACKUP_ID --databases-only`
(see [Going back past a schema change](#going-back-past-a-schema-change)).

When nothing was running before the update, there is nothing to go back to and nothing is
put back. When the tree is not a git checkout, the images go back but the compose file cannot;
the error says so.

### Monorepo

After the build, every unit the application records is restarted, all of them before any is
probed, so the stretch in which some workspaces run the new build and others the old one is
as short as it can be. Then each unit is probed on its own port with the application's health
check; a unit without a port (a worker) has to be running. One workspace that does not answer
fails the update, however many of its siblings do.

Deploy hooks run at the root of the monorepo, in the release phase: `pre_deploy` once the build
is done and before any unit restarts (a hook that fails restarts nothing), `post_deploy` once
every workspace answered. The project's Prisma migration (`pnpm db:migrate`, or Prisma's own
command) aborts the update when it fails, and is skipped when hooks are declared, as for any
other type; see [Hooks and schema-aware rollback](#hooks-and-schema-aware-rollback).

Going back checks the tree out at the previous commit and rebuilds it: `pnpm install`,
Prisma's client generated again (no migration runs: it cannot be undone, and the previous
commit's are already applied), `pnpm build`, and the tree handed back to the service user.
Then every unit is restarted and probed again. When the rebuild of the previous commit fails,
nothing is restarted on the half-built tree and the error says so, with the build's output.

Why a checkout and a rebuild rather than restoring the backup `noust update` takes first: the
backup leaves out `node_modules`, `.git` and the build output, so restoring it needs the same
install and build anyway; restoring replaces the whole tree, losing whatever the application
wrote into it since; and the update goes on without a backup when one cannot be taken. A
checkout rewrites tracked files only and leaves `.env` files and uploads where they are.

A monorepo that is not a git checkout has no commit to go back to: the update fails, the
workspaces keep running the new build, and the error names `noust rollback <domain>`, which
restores the backup taken before the update.

### Either type

The checkout after a failed update is detached at the previous commit, like `noust update
--commit`; the next `noust update` follows the branch again. Local changes to tracked files in
the tree are overwritten by the checkout, as they are by the update's own pull. A build that
fails before anything was restarted is not rolled back for a monorepo: the units keep running
the processes they had, and the tree is left at the new commit for the next update to fix.

## Builds in the sandbox

Installing dependencies and building run code from the repository and from every dependency's
install scripts. From 3.1 that happens in a sandbox rather than as root:

```bash
noust app sandbox self-test                 # does the sandbox hold on this server?
noust app sandbox status shop.example.com   # sandbox, or root and why
noust app sandbox test shop.example.com     # build what the next update builds, in the sandbox; nothing is activated
noust app sandbox enable shop.example.com   # sandboxed from the next deploy on
noust app sandbox enable shop.example.com --network strict
noust app sandbox disable shop.example.com --reason "..."   # build as root: recorded, audited
```

- **Who builds in it**: applications and previews created from 3.1 on, and every application
  where it was enabled. An application deployed before 3.1 still building as root is tried in
  the sandbox by Noust itself, once, on its **next update** (3.2): after the source is in place
  and before the build, the update builds what it is about to build in a scratch copy, as
  `noust app sandbox test` does. If that passes, the sandbox is turned on and the update builds
  in it; if it fails, the update builds as root exactly as before, the failure is recorded and
  warned about (the application's page, `noust health`, `noust ens check`) and sent as a
  `deploy_failed` notification with the build's output, and Noust does not try again by itself:
  fix the build and run `noust app sandbox test`, then `enable` (`--force` skips the test).
  Nothing is tried, and nothing changes, for an application whose operator recorded a decision
  to build as root (`noust app sandbox disable --reason`), for a type with nothing to build
  (static) or that builds in Docker (Compose), or on a server where the sandbox does not hold
  (the self-test fails: containers, WSL without mount namespaces).
- **What the build sees**: its release, read-write; its own cache under
  `/var/cache/noust/build/<app>`; the application's `.env`, handed in by systemd, which
  `noust-build` itself cannot open; a clean environment; no `/root`, no `/etc/noust`, no store,
  no other application's files. Memory and CPU are limited.
- **Installs get devDependencies**, as a root build's did: a root install never read `.env`, so
  a `NODE_ENV=production` there (or npm's and yarn's own production settings) is removed from
  the install's environment, and `npm ci`, `pnpm install` and `yarn install` keep the
  devDependencies the build needs. The build itself gets the whole `.env`.
- **`--network strict`**: dependencies install with the network but without the application's
  variables, and the build runs with them but without a network. A build that fails for want of
  the network says so and how to allow it. A preview follows its application's profile and
  never gets production secrets.
- **`--pty`** runs the build on a terminal, for build scripts that reopen `/dev/stderr`.
- **In place and monorepo**: the build runs in the live tree as the service account, still with
  the rest of the machine hidden. `noust app migrate` is the way to full separation. Their
  `test` builds a copy of the tracked files as they are in that tree, uncommitted changes
  included, and lists those changes so they can be committed.
- **Docker Compose** builds run inside the Docker daemon. A new stack with `privileged: true` or
  the Docker socket mounted is refused unless `noust app sandbox compose-exception DOMAIN`
  records why it needs them.
- **Fail closed**: where the self-test fails (containers and WSL without mount namespaces), a
  build stops with the evidence and never falls back to root; `disable` with a reason is the
  explicit way to build that application as root.
- A build killed for its time or memory limit says which; cancelling a deploy stops its build
  unit.

## The account an application runs as

Until 3.1 every application ran as the one configured service account (`www-data`), so a
compromised application could read and write every other application's tree. From 3.2 an
application created by Noust runs as an account of its own, `noust-app-<name>` (a long name is
shortened and given a hash, to stay within 32 characters): a system account with its own group,
no home and no login shell, which owns the application's tree, its `.env` and its caches. The
unit says `User=` that account; a PHP application's FPM pool runs as it, one pool per account.
The web server still reads what it serves: a deployed tree is world-readable except its `.env`
files. Deleting the application with its files removes its account too.

```bash
noust app identity status shop.example.com    # the account it runs as, and whether it is its own
noust app identity migrate shop.example.com   # move an existing application onto its own account
```

- **Applications from before 3.2 keep the shared account** through every redeploy until you
  move them. `migrate` creates the account, hands it the files the shared account owned (tree,
  `.env`, build cache), rewrites the unit or the pool and restarts the application behind the
  health gate. If it does not answer, the owners of the files it changed, the unit or pool and
  the records are put back exactly and the application is restarted as it was. Over the API
  it is `POST /api/apps/{domain}/identity/migrate`, which is root-equivalent, needs sudo mode
  and is subject to four-eyes approval when approvals apply.
- **Not every application can have one.** A Docker Compose stack (its processes are the
  containers'), a monorepo (several units, which stay on the shared account for now) and a
  static site (nothing runs) keep the shared account; `status` says why. An application in
  zero-downtime mode must have it turned off to migrate. Creating and migrating need root.

## Deploy history

Every deploy, update, activation and migration writes a row to the deployment history with
its build log, whatever the layout, monorepo and Docker Compose included. See it with the
console's Deployments tab or `GET /api/deployments?domain=<domain>`; the last 20 per
application are kept.

Since 3.2 a row also says what the deploy hooks did: `hooks` (each hook, and Prisma's automatic
migration, with its command, exit code, duration and the end of its output), `schema_changed`
(a migration changed the database's schema: going back past it asks first) and `warnings` (why a
successful deployment went live with warnings: a `post_deploy` hook failed). Older rows have
none of them. An adopted stack's history starts with one row, the adoption, at the commit that
was checked out. See [Hooks and schema-aware rollback](#hooks-and-schema-aware-rollback).
