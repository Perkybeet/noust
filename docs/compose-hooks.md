# noust.yaml, hooks and migrations in a stack

Part of [Docker Compose](compose.md).

## noust.yaml

A repository can tell Noust how to deploy it with a `noust.yaml` (or `.noust.yaml`) at its
root, versioned with the code. For a Compose stack it has two keys, both optional (a third, `build`,
changes how Node, Python and static applications are built and started: [build.md](build.md)):

```yaml
hooks:
  pre_deploy:                       # before the new version serves; the first failure stops the deploy
    - run: ./node_modules/.bin/prisma migrate deploy
      service: backend              # Compose only: the service whose new image runs it
      workdir: /app                 # optional: a path in the container, with 'service'
      timeout: 600                  # seconds, 1 to 3600 (600 when left out)
      migrates: true                # it changes the database schema (see below)
    - run: ./scripts/check-env.sh
      service: backend
  post_deploy:                      # after the new version passed its health gate
    - run: node scripts/purge-cache.js
      service: backend
      timeout: 120
backup:
  databases: auto                   # auto (the default) | off | a list, see "Stack database backups"
```

| Key | Meaning |
|---|---|
| `hooks.pre_deploy`, `hooks.post_deploy` | Lists of hooks, run in order. |
| `run` | The command: text, split the way a shell splits it (`shlex`), or a list of arguments. It is one program with its arguments, never given to a shell: `&&`, pipes, redirections and `$VARIABLES` do not work. For those, commit a script and run it. The program's path may not contain `..`. |
| `service` | Compose only: the service whose image runs the hook. Without it, the first service that has `build:`; a stack that builds nothing has to name one. Any other type refuses a hook that names a service. |
| `workdir` | Where it runs. With `service`, a path inside the container (`-w`). Without it, a directory of the tree being deployed, relative to its root, that must resolve inside it. `..` is refused. |
| `timeout` | Seconds the hook may take, from 1 to 3600. 600 when left out. A hook that runs out of time fails. |
| `migrates` | `true` when the hook changes the database schema. Default `false`. |
| `backup.databases` | `auto`, `off`, or a list of `{service, engine, database, user}`. See [Stack database backups](compose-backups.md#stack-database-backups-and-restore). |

A `noust.yaml` is input from the repository, like the rest of it, so it is read strictly:

- The schema is closed. A key Noust does not know is refused, naming it (`Unknown key
  'pre_deploys' in hooks`), because a misspelt phase would be a migration that silently never
  runs. Every refusal names the field (`hooks.pre_deploy[0].timeout`).
- Only one of `noust.yaml` and `.noust.yaml` may exist. It must be a regular file (a link is
  refused unread) of at most 64 KiB, in UTF-8.
- YAML reads a bare `off` as `false`. `databases: off` and `databases: false` both mean off.
- A hook never gets more privileges than the stack's compose file already gives its service,
  and a repository cannot choose another user, `privileged` or mounts for it.

### Where the hooks run

| Type | Where |
|---|---|
| Docker Compose | `docker compose run --rm --no-deps [-w WORKDIR] --entrypoint "" SERVICE COMMAND...`: a one-off container of the image the deployment just built, with the service's environment, networks and volumes, before any container is recreated. On the first deploy, where nothing serves yet, `--no-deps` is left out so Compose starts what the service depends on (the database a migration needs). |
| Applications on releases or in place | The release phase of the build, in the tree being built (the new release, or the tree in place), before the release is activated: as the application's own account, with its `.env`, when it builds in the sandbox, and as Noust itself (root) while it still builds as root, as its migrations always did. See [releases.md](builds-and-accounts.md#builds-in-the-sandbox). |
| Monorepo | The same, at the repository's root, after the build and before any unit restarts. |
| Static sites and Vite builds without a server | None. Declaring hooks for them is refused, saying why: nothing of such a site runs. |

Where `run` is a path (`./scripts/migrate.sh`), it is relative to the working directory the
hook runs in.

### What a hook failing means

- **`pre_deploy` fails** (a non-zero exit, or out of time): the deployment stops before anything
  serves the new version. What served before keeps serving, and the error carries the hook's own
  output, verbatim. Later `pre_deploy` hooks do not run.
- **`post_deploy` fails:** the new version is already serving, so nothing is undone. The
  deployment is recorded as deployed with warnings, the output is in its log and on its page in
  the console, and the `deploy_hook_failed` notification is sent (on by default,
  `notifications.events.deploy_hook_failed`, see [CONFIG.md](CONFIG.md)).
- The deployment's history keeps the last 4000 characters of each hook's output, with its
  exit code and duration. The log has all of it.

### migrates, and the database schema

A deployment is recorded as having changed the schema when a hook marked `migrates: true`
succeeded and its output does not say there was nothing to apply, or when Noust's automatic
Prisma migration applied at least one. Noust reads the output in the migration tools' own words
to tell: Prisma ("No pending migrations to apply"), Django, Laravel, Knex, Sequelize, TypeORM,
Doctrine, Flyway and golang-migrate. Output that says neither counts as a change: a schema that
may have changed is treated as one that did. This record is what makes [going back past the
deployment ask first](#rolling-back-past-a-migration).

### Prisma

Applications that use Prisma have always run `prisma migrate deploy` on their own (a Compose
stack does not: declare the migration as a hook). In 3.2:

- **A failure aborts the update.** Until 3.1 it was a warning, and the new code went on serving
  against the schema it was not written for. It is now handled like a failing `pre_deploy`:
  nothing is switched over and the error has Prisma's output.
- **Declared hooks win.** When any hook is declared, from `noust.yaml` or by the operator,
  Prisma's automatic migration does not run, and the log says so. Declare the migration
  yourself, as in the example above.

### An example

A stack with a NestJS backend, a Next.js frontend, Postgres and Redis, whose backend image
holds Prisma, and that purges a cache once a deployment serves:

```yaml
# noust.yaml, at the root of the repository
hooks:
  pre_deploy:
    - run: ./node_modules/.bin/prisma migrate deploy
      service: backend
      workdir: /app
      migrates: true
  post_deploy:
    - run: node scripts/purge-cache.js
      service: backend
      workdir: /app
      timeout: 120
```

`noust app hooks show shop.example.com` says which hooks the next deployment runs and where they
come from, and the deployment's page in the console lists what each one printed.

## Operator hooks

The server can declare hooks of its own for an application, without a commit: for a repository
you do not control, or to try a hook before committing it. They are a YAML document of the same
shape as `noust.yaml`, holding only `hooks`:

```bash
cat > hooks.yaml <<'EOF'
hooks:
  pre_deploy:
    - run: ./node_modules/.bin/prisma migrate deploy
      service: backend
      workdir: /app
      migrates: true
EOF
noust app hooks set shop.example.com --file hooks.yaml     # '--file -' reads standard input
noust app hooks show shop.example.com                      # what the next deployment runs, and where from
noust app hooks clear shop.example.com                     # the repository's noust.yaml applies again
```

- **They replace the repository's whole.** The two are never merged: what runs is always one
  document somebody wrote. A document that declares no hook still replaces the repository's,
  which is how to switch a repository's hooks off for one application.
- **`show`** prints where the hooks in force come from: `the operator's hooks`, `the
  repository's noust.yaml, in the code that runs now`, or `none declared`, and each hook with its
  service, working directory, timeout and whether it changes the schema (`--json` for scripts).
  When the repository's `noust.yaml` is not valid, it says so: the next deployment fails with
  that error until it is fixed or the operator's hooks replace it.
- A document is validated before anything is stored, with the same closed schema, and refused
  for a type that has no hooks. It may be up to 64 KiB.
- **A hook is code.** It runs with the application's identity and secrets on every deployment,
  so setting one is root-equivalent: the `root_equivalent` permission, sudo mode, and a second
  person's approval where four-eyes is on. Clearing needs `apps.manage`. Every change is
  recorded in the audit log as `apps.hooks`.

| Endpoint | Method, permission |
|---|---|
| `/api/apps/{domain}/hooks` | `GET` `apps.read`: the hooks in force, their `source` (`operator`, `repo` or `none`), the operator's `document` and `repository_error`. `PUT` `root_equivalent`, sudo, four-eyes: body `{"document": "<yaml>"}`. `DELETE` `apps.manage`: clear. |

In the console: the application's Settings > Deploy hooks shows what is in force and where it
comes from, and a field for the operator's own (saving asks you to confirm it is you).

## Rolling back past a migration

Noust puts code back, never a database. A deployment that changed the schema and is then gone
back past leaves the older code running against the newer schema, which is sometimes fine and
sometimes not, so since 3.2 that is a decision and not a click.

- **Every deployment records `schema_changed`**, as [described above](#migrates-and-the-database-schema).
  `GET /api/deployments` and `GET /api/deployments/{id}` carry it, and
  `schema_changed_between`: the later deployments of the same application that changed the
  schema, which going back to this one passes. The console shows them before the button is
  pressed.
- **Going back by hand asks.** Every way back is held to the same check: going back to a
  deployment (`POST /api/apps/{domain}/deployments/{id}/rollback`, the console), `noust releases
  rollback`, `noust update --commit` to an older commit, `noust rollback`, and restoring only
  the files of a backup taken before later deployments. Without confirmation they are refused
  with the deployments named and the way to go on.

  | Where | Confirm with |
  |---|---|
  | `noust releases rollback`, `noust rollback`, `noust update --commit`, `noust backup restore` | `--schema-changed-ok` |
  | `POST /api/apps/{domain}/deployments/{id}/rollback`, `POST /api/jobs/rollback`, `POST /api/backups/{id}/restore` | `"schema_changed_ok": true` in the body |
  | `POST /api/apps/{domain}/releases/{id}/activate` | `?schema_changed_ok=true` |
  | The console | The dialog "Going back passes a change to the database" lists the deployments and their migrations; **Go back anyway** confirms. |

  Without it, the API answers `409` with `"error": "schema_changed"` and `"deployments":
  [41, 42]`, the ids that changed the schema, oldest first.
- **Putting the database back too.** The refusal suggests the way: restore the copy taken
  before the first of those deployments. For a Compose stack, the update took one (see [Stack
  database backups](compose-backups.md#stack-database-backups-and-restore)): `noust backup list DOMAIN`, then
  `noust backup restore BACKUP_ID --databases-only`. A restore that also puts back a database
  the backup holds takes the schema back with it and is not asked.
- **The automatic way back still happens.** When the new version fails its health gate, the
  previous one is put back (the safe choice, as always). If the attempt changed the schema, the
  error starts with "The database schema was changed by this attempt and was not undone.", so
  it is the first thing read, and the deployment's page and the history mark it. With [the
  relay](compose-relay.md#zero-downtime-updates-the-relay) the usual failure, an image that does not start,
  never touches what serves.

See [releases.md](releases-hooks.md#hooks-and-schema-aware-rollback) for how this fits the other
ways back.
