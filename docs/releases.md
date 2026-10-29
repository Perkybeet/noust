# Releases

Noust builds every deploy of a new application in its own directory, switches to it
atomically, keeps it only if it answers, and can go back to any release still on disk in
seconds. This guide describes that layout, how a deploy moves through it, and how an
application deployed by 1.x is moved onto it.

The rest of this guide is on pages of its own:

- [Deploys: without downtime, in a queue, from the right commit](releases-deploys.md)
- [Configuring the health check](releases-health-check.md)
- [Hooks and schema-aware rollback](releases-hooks.md)
- [Moving an in-place application onto releases](releases-migrate.md)
- [Building on one server and running on another](releases-build-elsewhere.md)
- [Docker Compose and monorepo applications](releases-compose.md)
- [Releases and the health gate](how-releases-work.md)
- [Builds in the sandbox, and the account an application runs as](builds-and-accounts.md)

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
you confirm it: see [Hooks and schema-aware rollback](releases-hooks.md#hooks-and-schema-aware-rollback). Going
back never touches the database, with or without that confirmation: the rollback dialogs of the
console and the command both say so, because the older code runs against the data and schema
the newer one left.

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

### How going back is done

Going back to a deployment is not always a swap, and nobody should press the button expecting
one and wait for a build. `GET /api/deployments` and `GET /api/deployments/{id}` say it as
`rollback_method`, and the console shows it on the button before it is pressed:

| `rollback_method` | What happens | Applications |
|---|---|---|
| `instant` | Something already there is put back, behind the health gate: a release, a container application's image, the images a Compose stack kept. Nothing is built. | On releases; `container`; Docker Compose with its images [kept](releases-compose.md#kept-images-and-instant-rollback) |
| `rebuild` | The commit is checked out and built again where the application runs. | In place; a monorepo that was not moved onto releases; a Compose stack whose images were not kept |
| `snapshot` | The backup the next update took is restored, as a job. | In place with no commit to go back to |

An application in place that wants the first row can have it: `noust app migrate`.

### Releases and backups

`noust releases rollback` and `noust rollback` are different tools:

- `noust releases rollback` switches between builds that are already on disk. It is instant,
  and it does not touch `shared/`: the environment and uploaded files are the same before and
  after.
- `noust rollback` restores a backup archive, taking a safety backup of the current state
  first. It is the recovery tool for data and for in-place applications. See `noust backup`.

## Rolling back to a deployment

Every deployment in the history says whether it can be gone back to:
`GET /api/deployments` and `GET /api/deployments/{id}` carry `rollback_available` and, when it
is false, `rollback_unavailable_reason`. `POST /api/apps/{domain}/deployments/{id}/rollback`
queues it as a job (`202`), with `apps.deploy`; a deployment that cannot be gone back to
answers `409 rollback_unavailable` with the reason, and one that would pass a deployment that
changed the database schema answers `409 schema_changed` until the body says
`{"schema_changed_ok": true}` (see
[Hooks and schema-aware rollback](releases-hooks.md#hooks-and-schema-aware-rollback)).

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
  [Docker Compose and monorepo applications](releases-compose.md#docker-compose-and-monorepo-applications)). A
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
