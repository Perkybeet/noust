# Deploys: without downtime, in a queue, from the right commit

Part of [Releases](releases.md). Before an update or a rollback, [see what it will do](plans.md).

## Zero downtime by default

Activating a release by swapping `current` and restarting the unit leaves the application
without a process for as long as it takes to start. Since 4.0 an application **created** on the
releases layout that serves HTTP through a port behind nginx (Node.js, Next.js, Python, Vite)
avoids that from its first deployment: once the first release answers, it is turned into the
two instances of blue/green (`noust app zero-downtime DOMAIN on`, the same code, undone whole
when a step fails). Every update after it starts the new release on the idle colour, asks the
health gate on that instance's own port, switches nginx's upstream with `nginx -t` and a
reload, lets the old instance drain and stops it. A release that does not answer is stopped;
the old one never stopped serving, so there is nothing to put back.

- **Applications that exist keep how they update.** Nothing is converted behind an operator's
  back, a migration onto releases included. `noust health` lists the ones that could
  ("Updates") and `noust diagnose` names the command.
- **It never fails the deployment it follows.** An application whose site has routes of its
  own, whose next port is taken or that Apache serves stays as the single unit it was created
  as, and the log says why and how to try again.
- **Turning it off for the server**: `deploy.zero_downtime: false`. The drain
  (`deploy.drain_seconds`, 10 by default) is recorded for the application and changes with
  `noust app zero-downtime DOMAIN on --drain N`. The application must tolerate two copies
  running for a few seconds.
- **Static sites and PHP applications need none of it.** A static site has no process:
  activating a release is one rename of `current` over the old link, so every request resolves
  it to the old release before and the new one after, never to nothing, and nothing is
  restarted or reloaded. A PHP application is the same rename plus a graceful reload of PHP-FPM
  (the requests in flight finish on the old workers), never a stop or a restart. Tests hold
  both claims to the code. A Docker Compose stack keeps its relay, which stays opt-in.
- **Rollback, migration and limits that restart** pass the same health gate in either mode and
  put back what served before when it fails.

## The deploy queue

Two operations used to refuse each other with "busy": a push that arrived while an update ran
failed, and was lost. Now:

- **A push or a deploy request during a build waits its turn**, in order. The console's
  Jobs page and `noust deploy queue` show each request as `queued`, `running`, `succeeded`,
  `failed` or `cancelled`, with its place in the line and what it waits for.
- **Requests that are the same request are one**: several pushes while an application builds
  are one update that builds the branch as it is when its turn comes (the last push wins; for
  an application that follows tags, the newest tag announced).
- **Builds of different applications overlap**, up to `deploy.max_concurrent_builds` (2).
  One application never builds two things at once; anything that is not a build (a restore, an
  operating system update, a certificate) runs alone, in order, as it always did.
- **The queue is bounded**: with `deploy.queue_limit` (25) builds waiting, the next is refused
  with `429 queue_full` (webhooks answer 429 too, and the application's delivery log says why,
  so the forge retries).
- **A build has a time limit**: `deploy.max_minutes` (60; 0 is no limit) from the moment it starts.
  Past it the build is stopped the way an operator's cancel stops it, with its release removed and
  what served before untouched, and it ends as a failure that names the limit (not as a cancellation,
  so the notification, the job and `noust deploy queue` say it failed). Raise it for an application
  whose build is slow by design.
- **A terminal's update does not make the console's queue fail**: a build waits for an
  operation another process holds, says what it waits for, and gives up (naming it) only after
  an hour.
- The queue lives in the console's memory, as the jobs always did: a restart of the console
  marks what was queued or running as interrupted ([api.md](api.md#conventions)), and the next
  push queues its update again.

## Cancelling a deployment

```bash
noust deploy queue                       # what builds and what waits
noust deploy cancel shop.example.com     # stop the build of an application
noust deploy cancel a1b2c3d4             # or name the job
noust deploy cancel shop.example.com --all   # also what waits for it
```

An operator can stop a deploy or an update **until its release starts to be activated**: the
fetch, the install and the build run in a transient systemd unit, which is stopped (the build's
whole process tree with it); the half-built release is removed; the deployment is recorded as
cancelled, with who cancelled it (its status stays `failed`, `cancelled` says why); the
application's lock is released and the queue moves on. What served before is never touched,
because a release builds beside it and `current` has not moved. It works on a deployment started
anywhere: the console (`POST /api/jobs/{id}/cancel`), a webhook, another terminal. The request is
addressed to the operation that holds the application's lock, and a terminal is told how it
ended (cancelled, too late, or finished first).

What cannot be cancelled, and says so: a release that is **already being activated** (it
finishes, or puts back what served by itself; roll back afterwards if it is not what you want),
a migration, a restore or a deletion (they are halfway through changing the system) and a build in
place (it runs in the tree that serves: stopping it would leave the application broken). A
migration declared in a deploy hook is never interrupted either: a cancellation arriving while it
runs takes effect before the next step.

**A Docker Compose stack, a container application and a monorepo on releases can be stopped
too.** The images of a stack or a container are built by the Docker daemon, which stops the build
when the `docker` command that asked for it is killed; the containers that serve are replaced only
once the build is done. What the update changed to get there goes back exactly as after a build
that failed: the tree a stack pulled in place is checked out at the commit that serves, its
`.env` is put back, and a container application keeps the compose file and the image that serve
(no failed release is recorded, and the image it had begun is removed). A monorepo on releases
builds in a release of its own, which is removed like any other. A monorepo that builds in place,
in the tree its units run from, is the exception that remains.

## Rebuilding a deployment's commit

Update pulls the head of the branch. To deploy exactly the commit an earlier deployment was
built from, name it:

```bash
noust update shop.example.com --commit 9f8e7d6     # full or abbreviated, at least 4 characters
```

In the console, "Redeploy" on a deployment's page does the same:
`POST /api/apps/{domain}/deployments/{id}/rebuild` queues the update job with that
deployment's commit and answers `202` with the job. It needs `apps.deploy`, like an
update, and answers `409 no_commit` for a deployment whose source is not git. With
`{"no_cache": true}` in the body (the console's "Retry without cache" on a failed deployment) the
commit is built from scratch: no release on disk is switched to, and the package manager starts
from an empty cache.

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
  remembered in the repository's own configuration (`noust.branch`), so the next
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
   shows it, and `--unpin` removes it. In the console: the application's Code and build.
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
was checked out. See [Hooks and schema-aware rollback](releases-hooks.md#hooks-and-schema-aware-rollback).

Since 4.0 a deployment's log can be read by what matters: `noust app deploy-log DOMAIN [ID]
--level error --search text --sanitize`, the same filters on `GET /api/deployments/{id}/log`, and
in the console's deployment page. `--sanitize` masks what only looks like a credential, to
share the log. See [runtime.md](runtime.md#reading-a-deployments-log). A row also says, as
`rollback_method`, how going back to it is done ([above](releases.md#how-going-back-is-done)).

Since 4.0 a deployment an operator cancelled while it built ends with the status `failed` and
says so with `cancelled`, `cancelled_at` and `cancelled_by` (the status column cannot hold a
new value on a store from before 4.0): the overview does not count it as a failure and does not
list the application as needing attention, `noust diagnose` does not blame it, and a
notification channel is not told it failed. See [Cancelling a deployment](#cancelling-a-deployment).
