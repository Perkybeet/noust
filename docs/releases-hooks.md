# Hooks and schema-aware rollback

Part of [Releases](releases.md).

A project often has something to run around a deploy that Noust cannot know: apply the database
migrations before the new code serves, purge a cache after it does. And going back to older code
is safe for the code and not for the database: Noust puts code back, never a database. 3.2 adds
hooks for the first and a confirmation for the second.

## Deploy hooks

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
operator's hooks are in [compose.md](compose-hooks.md#noustyaml) and
[compose.md](compose-hooks.md#operator-hooks). What every type shares:

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
  [compose.md](compose-hooks.md#noustyaml).

## Prisma

A project that uses Prisma and declares no hooks keeps getting `prisma migrate deploy` after the
install, as before. Two things changed in 3.2:

- **A failing migration now ends the update.** Until 3.1 its failure was a warning in the log
  and the new code went on to serve against the schema it was not written for. It is treated
  as a failed `pre_deploy` hook: nothing is switched over and the error carries Prisma's own
  output.
- **Declared hooks replace it.** When `noust.yaml` or the operator declares any hook, the
  automatic migration does not run and the log says so. What the project states explicitly wins.

## What the history records

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

## Going back past a schema change

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
  [compose.md](compose-hooks.md#rolling-back-past-a-migration)); `--databases-only` puts back just
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
[Docker Compose](releases-compose.md#docker-compose) below and [compose.md](compose-backups.md#stack-database-backups-and-restore).
