# Docker Compose and monorepo applications

Part of [Releases](releases.md).

Neither type builds releases by default, so an update rebuilds it in place (a monorepo can
[move onto releases](#a-monorepo-on-releases), which replaces what follows for it). Since 2.1 the update records
what is serving before it touches anything, judges the result with the same health check as
a release (the path, statuses and timeout [configured](releases-health-check.md#configuring-the-health-check) for the
application), and puts back what was serving when the result does not pass. The update then
fails with the evidence verbatim, and its row in the deployment history is `failed`, recording
the commit that failed.

What "what was serving" means is the commit the tree was on before the update pulled, plus,
for a stack, the image each running container was created from.

## Docker Compose

Before the steps below, the update takes its usual backup, and for a stack that backup carries a
dump of each database the stack runs (Postgres, MySQL, MariaDB and MongoDB, found in the
compose file). The copy is on by default and **stops the update when it cannot be made**,
because without it a migration cannot be undone; `noust app backup-before-update DOMAIN off`
(or `backup.databases: off` in `noust.yaml`) turns it off. See
[compose.md](compose.md#stack-database-backups-and-restore).

1. **Record what serves.** `docker compose ps -q` lists the running containers and
   `docker inspect` reads, for each, the image it runs, the image name it was created from and
   its Compose service and project. Each image is also tagged
   `<project>-<service>:noust_previous`, because the build is about to move the service's own
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
(see [Going back past a schema change](releases-hooks.md#going-back-past-a-schema-change)).

When nothing was running before the update, there is nothing to go back to and nothing is
put back. When the tree is not a git checkout, the images go back but the compose file cannot;
the error says so.

### Kept images and instant rollback

Since 4.0 a stack that builds its images from a repository keeps the images of the releases it
served, so going back to one is the same instant operation it is on the release layout, instead
of building the old commit again (minutes, and everything the build needed).

- **What is kept.** After a deploy or an update passes, the image of every service that runs is
  tagged `<project>-<service>:noust-<release id>`, because Docker deletes an image that has no
  name (`docker image prune`); the ones pulled from a registry too. A release is recorded with its
  commit, when it was created and last activated, and whether its images are still on the host.
  It needs a git checkout with a known commit: a tree that is not one keeps nothing. When the
  images cannot be kept (Docker refuses a tag), the update still succeeds and says that going
  back to it will rebuild.
- **How many.** The application's retention, never fewer than the one serving and the one a
  rollback goes to. `noust releases keep DOMAIN N` shows or sets it (1 to 50); the images of the
  releases beyond it are removed with the release, oldest first. Only the name goes: an image a
  container or another name still uses stays, and the failure is reported.
- **Going back.** The images are named again as the compose file looks them up, the containers
  are recreated with `docker compose up -d --no-build --remove-orphans`, and the stack is judged
  like after an update. If it does not pass, what served before is put back. The tree is put on
  the release's commit first, so the compose file and the images go together.
- **The same commands as a release.** `noust releases list`, `noust releases rollback` and
  `noust releases keep` work on a stack, and the console's Deployments tab lists its releases
  with **Activate** on each one whose images are present. A release whose images were pruned
  appears as not on disk and is rebuilt (`rebuild`) when you go back to its deployment.
- **A schema change is still a decision.** Going back past a deployment that changed the
  database asks first, exactly as for any other application: the images go back, the database does
  not. See [Going back past a schema change](releases-hooks.md#going-back-past-a-schema-change).

A container application (`--type container`) keeps one image per release by its own mechanism
and goes back the same way. A stack that was only adopted has no release until its first
update.

## Monorepo

After the build, every unit the application records is restarted, all of them before any is
probed, so the stretch in which some workspaces run the new build and others the old one is
as short as it can be. Then each unit is probed on its own port with the application's health
check; a unit without a port (a worker) has to be running. One workspace that does not answer
fails the update, however many of its siblings do.

Deploy hooks run at the root of the monorepo, in the release phase: `pre_deploy` once the build
is done and before any unit restarts (a hook that fails restarts nothing), `post_deploy` once
every workspace answered. The project's Prisma migration (`pnpm db:migrate`, or Prisma's own
command) aborts the update when it fails, and is skipped when hooks are declared, as for any
other type; see [Hooks and schema-aware rollback](releases-hooks.md#hooks-and-schema-aware-rollback).

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

## A monorepo on releases

An update of a monorepo in place rebuilds the tree its workspaces run from, so going back after a bad
update meant building the previous commit again, with every workspace on the new build, or down, while
it did. A monorepo can have the instant rollback every other application has:

```bash
noust --dry-run app migrate mono.example.com   # the plan: every unit it will stop and rewrite
noust app migrate mono.example.com
noust update mono.example.com                  # builds releases/<id>, switches every workspace to it
noust releases rollback mono.example.com       # one link and a restart, no build
```

- **The migration is the same one, for every unit.** The live tree becomes the first release, the
  `.env` moves to `shared/`, and the unit of **each** workspace is stopped, rewritten to run from
  `current/<workspace>` and started again; it is undone exactly when any of them does not answer. A
  monorepo is never moved by an update or a redeploy: a deployment over one that is on releases is
  refused, and says to update it.
- **An update builds a release of its own** (`releases/<id>`), in the build sandbox as `noust-build` with
  `shared/` visible read-only, and hands it to the units' account after the build. The order is the other
  types': fetch, install, Prisma, build, the `pre_deploy` hooks at the root, and only then the switch.
- **The switch is gated on every workspace.** `current` is pointed at the new release, every unit is
  restarted before any is probed, and each unit with a port is probed on it. When one does not answer, the
  release that served is put back and every unit restarted on it, and the update fails with the probes and
  the journal of the one that did not answer. A release that failed is removed.
- **Each workspace's environment file travels by copy.** `apps/web/.env.production` is written by the
  deployment, not by the repository, and the unit loads it. A new release is given a copy of the one the
  release that serves has (mode 0600; a link in the repository is never followed), so a release keeps the
  configuration it ran with and going back restores it. The application's `.env` is linked into `shared/`
  as for any application.
- **Everything about releases works:** `noust releases list`, `rollback`, `keep`, the console's
  deployment history (which offers the swap as the `instant` way back) and `update --commit`.
  Previews, promotion between applications and release bundles are not offered for a monorepo: its
  releases hold several programs and only its own deployer builds them.

## Either type

The checkout after a failed update is detached at the previous commit, like `noust update
--commit`; the next `noust update` follows the branch again. Local changes to tracked files in
the tree are overwritten by the checkout, as they are by the update's own pull. A build that
fails before anything was restarted is not rolled back for a monorepo: the units keep running
the processes they had, and the tree is left at the new commit for the next update to fix.
