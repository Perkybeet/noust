# Process types and limits

Part of [Build, start and runtime](build.md).

## Process types

A repository can say what it runs in one place, as Heroku's Procfile does, or under `processes:`
in `noust.yaml`:

```yaml
processes:
  web: bundle exec puma -b tcp://127.0.0.1:${PORT}
  release: bundle exec rails db:migrate
  worker: bundle exec sidekiq
```

```
web: ./bin/server --port $PORT
release: ./bin/migrate
worker: ./bin/jobs
```

- `web` is the command of the application's unit, when the build block gives no `start` (the
  operator's or the repository's, which win) and the type runs a process.
- `release` runs as the only `pre_deploy` hook when no hooks are declared (the operator's, or the
  repository's `hooks:`, win whole): the deployment stops before any traffic moves when it fails.
  It does not mark itself as migrating the schema; declare it under `hooks.pre_deploy` with
  `migrates: true` for that.
- Every other process type (`worker`, `clock`, ...) is a process of the application that runs beside
  the web one, from the same release, with the account, environment and sandbox of its unit
  ([runtime.md](runtime.md#processes)). The deployment records it with the repository as its source,
  writes its unit before the release is activated, and the health gate restarts it with the web
  process and judges them together: a worker that does not stay up puts the release back. Starting it
  is the repository's say; how many copies run is yours (`noust app process scale`), and a process
  you define with `noust app process set` under the same name always wins over the repository's.
  Dropping a type from the file stops it once the new release holds. Only applications on the
  releases layout run them: one deployed in place says so in the deployment's log (and in `noust app
  process-types show`) and `noust app migrate` moves it. A declared name a unit cannot have (a
  reserved one, an underscore, too long) is skipped with the reason, never a failed deployment.

A command is an argument vector, never a shell line. A Procfile line that needs a shell (`&&`, a
pipe, a redirection, a substitution) is refused with the way out (a script of the repository), and
leading `NAME=value` words run the program through `env`. A `processes:` block wins over a
`Procfile` whole.

**A Procfile is read by new applications only.** A repository that carried a Procfile before
Noust read one keeps starting the way it was deployed: an update never starts reading it, because
that would change a working application's start command and run a `release` command on a database
nothing had ever migrated that way. An application deployed from its Procfile remembers it, and a
`processes:` block in `noust.yaml` is always read. A Procfile that cannot be used (a line that needs a
shell, a link, too large) is a warning in the deployment's log and `noust app process-types show`, never
a failed deployment. `noust app process-types show DOMAIN` says which of the declared types the next
deployment uses and why one is not in effect.

## Limits

- **Blue/green** cannot serve a project from a subdirectory: both instances run from the root of
  their release. The deployment refuses it, naming the way out.
- **PHP** is served by nginx or by Apache (`mod_proxy_fcgi`, enabled with the site; a `.htaccess` is never read) from its web root (`webroot` in the PHP settings, which also serves a
  project in a subdirectory); `base_directory` is refused.
- **A pinned PHP** that is not installed is installed with apt only; on other distributions the
  error says how to install it by hand.
- **The resource monitor** samples each PHP application's workers in the PHP-FPM service that holds
  its pool, so applications pinned to different versions are all measured; the service of every
  version is asked about once.
- **Rolling back** to a release keeps the unit of the latest deployment: going back past a
  change of `start`, `base_directory` or runtime does not put the old unit back. Deploy the
  older commit instead (`noust update --commit`): it is built again with the block that commit carries (or the operator's) and the unit follows.
- **Cron jobs** of an application run in the root of its release, not in its base directory.
- A tree that does not exist yet (before the fetch, under `--dry-run`) has no build block: a
  rehearsal shows the type's commands.
