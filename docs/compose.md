# Docker Compose

How Noust deploys, updates and looks after a Docker Compose stack, and the 3.2 features that
a stack gets first: deploy hooks and migrations, going back past a migration, updates without a
cut, adopting a stack that already runs, copies of its databases, deploying by tag and workers
that publish no port. Hooks, the rollback check, the site you wrote yourself and deploying by
tag also apply to other application types where this page says so.

## What Noust does with a Compose stack

A stack is deployed in place: the directory holds the checkout, the `.env`, and whatever the
containers write next to the code. It runs under one systemd unit, `<app>.service`, which runs
`docker compose up -d` and `down`, and an update rebuilds the images, recreates the
containers, judges the result with the same health gate as any other application and puts back
the images that served when it does not pass. That part is unchanged since 2.1 and is
described in [releases.md](releases.md#docker-compose-and-monorepo-applications). A stack that
publishes no port is a worker: no site, no certificate, judged by its containers.

What 3.2 adds, each described below:

- [`noust.yaml`](#noustyaml): a file in the repository that hangs commands on a deployment
  (`pre_deploy`, `post_deploy`) and declares the stack's databases. [Operator
  hooks](#operator-hooks) replace it from the server.
- [A migration that fails stops the update](#noustyaml), and a deployment records whether it
  changed the database schema, so [going back past it asks first](#rolling-back-past-a-migration).
- [Updates without a cut](#zero-downtime-updates-the-relay): each web service is recreated
  while a twin of it serves.
- [A site you wrote yourself](#your-own-site) is never rewritten or deleted.
- [Adopting a stack that already runs](#adopting-a-running-stack), without touching it.
- [A copy of the stack's databases before every update](#stack-database-backups-and-restore),
  and a restore that puts them back.
- [Deploying a tag or a release](#deploying-by-tag) instead of a branch.
- [Workers without a web port](#workers-without-a-web-port) are judged by their containers.
- [Which service gets `/`](#which-service-gets-the-root-route), and more
  [options for `noust.nginx.yaml`](#routing-with-noustnginxyaml).
- [Smaller fixes](#other-things-that-changed-for-compose): ports, volume names, placeholders.

Nothing changes for an existing stack until its operator asks, except that an update now copies
the stack's databases first (and stops if it cannot) and the fixes under [Other things that
changed](#other-things-that-changed-for-compose).

## noust.yaml

A repository can tell Noust how to deploy it with a `noust.yaml` (or `.noust.yaml`) at its
root, versioned with the code. It has two keys, both optional:

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
| `backup.databases` | `auto`, `off`, or a list of `{service, engine, database, user}`. See [Stack database backups](#stack-database-backups-and-restore). |

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
| Applications on releases or in place | The release phase of the build, in the tree being built (the new release, or the tree in place), before the release is activated: as the application's own account, with its `.env`, when it builds in the sandbox, and as Noust itself (root) while it still builds as root, as its migrations always did. See [releases.md](releases.md#builds-in-the-sandbox). |
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

`noust app hooks show proggest.es` says which hooks the next deployment runs and where they
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
noust app hooks set proggest.es --file hooks.yaml     # '--file -' reads standard input
noust app hooks show proggest.es                      # what the next deployment runs, and where from
noust app hooks clear proggest.es                     # the repository's noust.yaml applies again
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
  database backups](#stack-database-backups-and-restore)): `noust backup list DOMAIN`, then
  `noust backup restore BACKUP_ID --databases-only`. A restore that also puts back a database
  the backup holds takes the schema back with it and is not asked.
- **The automatic way back still happens.** When the new version fails its health gate, the
  previous one is put back (the safe choice, as always). If the attempt changed the schema, the
  error starts with "The database schema was changed by this attempt and was not undone.", so
  it is the first thing read, and the deployment's page and the history mark it. With [the
  relay](#zero-downtime-updates-the-relay) the usual failure, an image that does not start,
  never touches what serves.

See [releases.md](releases.md#hooks-and-schema-aware-rollback) for how this fits the other
ways back.

## Zero-downtime updates (the relay)

`docker compose up -d` recreates a web service by stopping its container and starting the new
one: for as long as the new one takes to listen, nginx has nobody on the port. For a backend
that takes fifteen seconds to start, that is fifteen seconds of `502`. With the relay on, Noust
recreates each web service while a twin of it serves.

Measured in the project's integration harness, under a request every 200 ms through nginx: a
plain recreate failed 72 of 94 requests; the relay failed 0 of 110. On a real Next.js and
NestJS stack (Proggest), an update that also applied a Prisma migration failed 0 of 316 to 329
requests, and an update whose backend could not start was refused at the relay with 0 of 687.

```bash
noust app zero-downtime proggest.es on               # the mode; --drain SECONDS (0 to 300, default 10)
noust app zero-downtime proggest.es                  # the mode, and what is missing for it
noust app zero-downtime proggest.es off
```

The same command and endpoint (`GET|PUT /api/apps/{domain}/zero-downtime`) as for blue/green
applications; for a stack it means the relay. Turning it on restarts nothing: the next update
is the first one relayed.

### What it needs

- **nginx as the web server.** A stack is only ever served through nginx.
- **Every web service the site reaches publishes a TCP port at a fixed host port**:
  `"127.0.0.1:3000:3000"` or `"3000:3000"`. A port Docker picks (`"3000"`) cannot be reached at
  a known address.
- **The site reaches each service through a servers file** of Noust's:
  `/etc/nginx/noust-upstreams/<app>/<service>.servers`, where `<app>` is the domain with dots
  as dashes (`proggest-es`). It holds only lines of the form `server 127.0.0.1:3000;`: the
  service's own port normally, the relay's while one serves. The site includes it inside an
  `upstream` block.
  - **A site Noust wrote** is rendered with those blocks when you turn the mode on, named
    `wasm_bg_<app>_<service>`, each with `keepalive 64;`. Nothing for you to do.
  - **A site you wrote** includes the file itself, inside the upstream block that proxies to
    the service, keeping your names. Noust never edits it.

### Including the servers file in your own site

Take the upstream block that proxies to the service and replace its `server` line with the
`include`:

```nginx
# before
upstream nestjs_upstream {
    server 127.0.0.1:3000;
    keepalive 64;
}

# after
upstream nestjs_upstream {
    include /etc/nginx/noust-upstreams/proggest-es/backend.servers;
    keepalive 64;
}
```

The order of operations, because the file has to exist for nginx to accept the include:

1. Run `noust app zero-downtime proggest.es on`. It writes the servers files first, each naming
   its service's own port, then checks your site and, for every service it does not include,
   refuses and prints the exact line that is missing and which file it goes in. It changes
   nothing in your site.
2. Add the line, then `nginx -t && systemctl reload nginx`. The site behaves exactly as before:
   the file names the same port you had.
3. Run `noust app zero-downtime proggest.es on` again. It finds the include and turns the mode
   on.

`noust app zero-downtime proggest.es` shows the same refusal at any time, with `eligible` and
`reason` in `GET /api/apps/{domain}/zero-downtime`.

### What an update does

The web services the site reaches through a servers file are relayed one at a time, in
`depends_on` order: a backend before the front that calls it, so a new front never talks to an
old backend. For each service:

1. `docker compose run -d --no-deps --use-aliases --name <project>-<service>-relay --publish
   127.0.0.1:<port>:<container port> <service>` starts the new image on a free loopback port
   from 20000 to 29999. `--use-aliases` makes the relay answer to the service's name inside the
   stack's network too.
2. The **health gate** asks the relay on that port (the application's health path for the
   service the site serves `/` from, the default path for the others). If it does not answer,
   the update stops here: the service's own container was never touched, and an image that does
   not start is exactly what this proves. The error has the probes and the relay's last 40 lines.
3. The servers file names the relay; `nginx -t` runs, nginx reloads, and the old container
   finishes its requests for the drain time (`--drain`, 10 seconds by default).
4. `docker compose up -d --no-deps --force-recreate <service>` recreates the service's own
   container, and the gate asks it on its own port.
5. The servers file names the service again, nginx reloads, the relay drains, and
   `docker stop -t 20` stops it and `docker rm -f` removes it.

Then the rest of the stack (databases, queues, workers, and any web service the site does not
reach through a servers file) is brought up with `docker compose up -d --remove-orphans`, as
before.

When something goes wrong, the failure leaves a site that still answers:

- **The relay does not answer:** the service is untouched and the update fails. Nothing moved.
- **The recreated container does not answer:** the relay, with the new image, keeps serving, and
  the error says which relay and on which port. See why with `docker compose logs <service>`.
- **nginx refuses the switch:** that service is recreated with a cut and the log says why. The
  same when no port is free for a relay.
- **An update that was killed midway** can leave a relay serving. The next update resolves it
  before anything is rebuilt: when the servers file still names a relay and the service's own
  container answers, traffic goes back to it and the relay is removed; when the service does not
  answer, nothing is touched and the error says why. `noust setup doctor` lists such leftovers
  under "Docker Compose relays", with the command that resolves them, and so does turning the
  mode off.

Two versions of a relayed service run side by side for a few seconds. A queue consumer inside a
web process, or a job scheduled in-process, may run twice in that window: that is the project's
to tolerate, and the reason the mode is opt-in.

### Turning it off

`noust app zero-downtime proggest.es off` gives any relay still serving back to its container,
puts the stack back on plain recreates and, for a site Noust wrote, renders it again to proxy to
the ports directly and removes its servers files. A site you wrote keeps its `include` lines and
the files stay, each naming its service's own port, so nginx keeps loading it.

## Your own site

An application's site is a file Noust writes, carrying the line "Generated by Noust". A site
without that line was written by someone else, and since 3.2 no deployer rewrites it or deletes
it, whatever the type: deploy, update, certificates, a failed deploy's undo and deleting the
application all leave it alone. 3.1 respected it for single-process applications only; a Compose
stack's was replaced on every deploy, and a certificate step deleted it.

- What Noust would need it to contain is checked, and reported as warnings with their fix:
  in the deployment's log, a `server_name` that lacks a name recorded for the application
  ("Its server_name does not include ..."), and, for [the
  relay](#zero-downtime-updates-the-relay), a site that includes no servers file of Noust's
  (the update then recreates the containers with a cut and says how to see what is missing).
  `noust app zero-downtime DOMAIN` prints the exact line a site needs.
- **`noust delete`** removes everything else and says what it kept: "has a nginx site
  configuration Noust did not write; it is left in place, and its certificate with it", and, for
  a stack that was relayed, that the servers files stay while the site includes them. Remove them
  by hand when nothing should answer on the name: `noust site delete DOMAIN`.
- **A site whose file is not named after the domain** (`proggest` for `proggest.es`) is found by
  the names it serves when the application is [adopted](#adopting-a-running-stack), and its name
  is recorded (`site_name`). Every command that turns a domain into a file asks that one place,
  so `noust site`, the certificates and the console all find it.
- **Aliases and certificates.** Docker Compose and monorepo applications now accept
  `noust domain add`, as every other type does. For a site you wrote, Noust records the name
  and warns that its `server_name` does not list it: add it yourself.

See [domains.md](domains.md#operator-sites) for domains and aliases.

## Adopting a running stack

A stack that somebody brought up by hand (Proggest, in `/opt/proggest`, started by its own
`deploy.sh`, served by its own nginx file) cannot be deployed with `noust create`: that would
clone into a directory that holds its data, derive a project name the running containers do not
have (and with it new, empty volumes) and write a second site next to yours. `noust app adopt`
registers it as it is.

```bash
noust --dry-run app adopt proggest.es --path /opt/proggest   # what it would record; changes nothing
noust app adopt proggest.es --path /opt/proggest             # shows the same, asks, adopts
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

### What it requires

- `DOMAIN` is not deployed yet, and no unit called `<app>.service` exists.
- `--path` is a git checkout with an `origin` (or `--source` names where to fetch from): an
  update brings an adopted stack up to date by resetting the checkout to the branch.
- Docker Compose 2.20 or later, for `up --dry-run`.

### What it does

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
5. **Registers the application** in place, at its real path (`/opt/proggest`, outside the apps
   directory), with its history starting at one entry for the adoption, at the commit that is
   checked out. A stack that publishes no port is registered as a worker, without a site.

It never clones, cleans, rebuilds, restarts or stops anything, and it never edits the directory
or the site. A checkout with changes to tracked files is adopted with a warning: the next update
resets them to the branch, while files git does not track (`.env`, bind-mounted data) are kept.

### Updates afterwards

`noust update` on an adopted stack fetches and resets the checkout to the branch (never
`git clean`), builds, runs the hooks, recreates the containers and judges the result, like any
Compose update. The pre-update backup and everything else use the application's recorded path,
not a directory under `/var/www/apps`.

### Proggest

Proggest is a monorepo with a NestJS backend, a Next.js frontend, Postgres and Redis,
`docker-compose.prod.yml`, Prisma migrations and a hand-written nginx site, `proggest`, serving
`proggest.es`. Brought up by its own `deploy.sh` under `/opt/proggest`, it adopts, and then gains
migrations as hooks, a copy of Postgres before each update and an update without a cut:

```bash
# 1. adopt it, as it runs
noust --dry-run app adopt proggest.es --path /opt/proggest
noust app adopt proggest.es --path /opt/proggest -y

# 2. in the repository, declare the migration as a hook (see "noust.yaml") and commit it
#    hooks.pre_deploy: ./node_modules/.bin/prisma migrate deploy, in the backend, migrates: true

# 3. let nginx reach the backend and the frontend through the servers files
noust app zero-downtime proggest.es on
#    refused, with the include line for each upstream block of /etc/nginx/sites-available/proggest:
#      include /etc/nginx/noust-upstreams/proggest-es/backend.servers;
#      include /etc/nginx/noust-upstreams/proggest-es/frontend.servers;
#    add them in place of the `server 127.0.0.1:PORT;` lines, nginx -t, reload, run it again
noust app zero-downtime proggest.es on

# 4. from now on
noust update proggest.es
```

The update dumps Postgres into its pre-update backup, runs the migration in a one-off container
of the new backend image before anything is recreated, recreates the backend and the frontend
behind their relays (the backend first: the frontend depends on it) and, if the backend cannot
start, stops without having touched what serves.

### Deleting an adopted stack

`noust delete proggest.es` takes the stack's containers down, removes its unit, the images Noust
kept for going back and the records, and leaves what Noust did not create:

- **The directory stays** whatever `--keep-files` says, because it is outside Noust's apps
  directory and was not created by Noust, with everything in it. To remove it with the
  application, name it exactly: `noust delete proggest.es --remove-adopted-directory /opt/proggest`
  (`remove_adopted_directory` in the API). A different path is refused before anything is
  touched.
- **Your site stays,** with its certificate, and so do the servers files it includes. Both are
  reported as kept, with how to remove them, not as a failure.
- **Noust's own servers files** for a stack that was relayed, when no site of yours includes them,
  are removed with it.

## A stack running outside its unit

When someone runs `docker compose up -d` by hand while the stack's unit is stopped, the site
serves but Noust does not supervise it, and a reboot would not bring it back: the unit is what
starts it at boot. Noust shows that as its own state, **Running outside Noust**
(`running_unmanaged` in the API), never as stopped: `noust list`, `noust health`, the console,
the overview and the fleet summary all say it, and the monitor announces it once (a warning
under the "Service down, and back" switch), not again until it has stopped running outside.

Only stacks whose unit exists and is stopped are looked at, all of them with one `docker ps`; a
unit that failed stays failed.

Hand it back to its unit:

```bash
noust app reclaim convertidordepdf.com
```

or "Hand it back to Noust" on the application's page (`POST /api/apps/{domain}/reclaim`, sudo
mode). The unit is enabled and started. Starting it runs `docker compose up -d`, which leaves
containers that already run as the compose file says exactly as they are; that is proven first
with the same `docker compose up --dry-run` adoption uses, and a start that would recreate
something is refused with Compose's output until you accept it (`--accept-recreate`,
`accept_recreate` in the API).

## Stack database backups and restore

Going back to the previous containers puts the previous code back, never the data: a migration
the failed attempt ran stays in the database. Before 3.2 the copy an update took held, at best, a
tarball of a live database's data directory, which nobody can restore. Now it holds a dump.

**What is detected.** The services whose image is the official `postgres`, `mysql`, `mariadb`
or `mongo` (any tag, `-alpine` included, from Docker Hub). The user and database come from the
service's environment as Compose resolves it (`docker compose config --format json`), so a
`${DB_USER}` filled from `.env` is what the container really has: `POSTGRES_USER` and
`POSTGRES_DB`, `MYSQL_*` or `MARIADB_*`, `MONGO_INITDB_*`. Other images (`bitnami/postgresql`,
`postgis/postgis`) are not detected; declare them:

```yaml
# noust.yaml
backup:
  databases:
    - service: db
      engine: postgres        # postgres | mysql | mariadb | mongo
      database: app           # optional: read from the service's environment
      user: app               # optional: the same
```

A list replaces detection. A user or database name has to be plain (letters, digits and `_ . @
$ -`, starting with a letter or digit); for a database that is not one of the four engines, back
it up with a `pre_deploy` hook of your own.

**How it is dumped.** Through the engine's own client, inside the service's container, with
`docker compose exec -T`:

| Engine | Dump | Restore |
|---|---|---|
| Postgres | `pg_dump -Fc` | `pg_restore --single-transaction --clean --if-exists` |
| MySQL | `mysqldump --single-transaction --no-tablespaces` | `mysql` |
| MariaDB | `mariadb-dump` (or `mysqldump`) `--single-transaction` | `mariadb` (or `mysql`) |
| MongoDB | `mongodump --archive` | `mongorestore --archive --drop` |

The password is read inside the container, from its own environment (`POSTGRES_PASSWORD`,
`MYSQL_ROOT_PASSWORD`, `MONGO_INITDB_ROOT_PASSWORD`...) or from the file a `*_FILE` variable
names, and handed to the client the way it takes a secret outside a command line. It is in no
argument on the host or in the container. The dump goes straight to a compressed file, mode
`0600`, named `stack-<service>-<engine>-<database>.<dump|sql|archive>.gz`, under
`wasm-backup/databases/` in the backup's archive, with a manifest entry that says how to put it
back.

**When.** In the backup every update of a Compose stack takes first, before the `pre_deploy`
hooks run and before anything is recreated. It is on by default for a stack that has databases.
`noust backup create DOMAIN --include-databases` includes them in a backup of your own, and that
is not switched off by `backup.databases: off`, which is about the automatic copy.

**If the copy fails, the update stops.** Without it there is no way back from a migration. The
error has the engine's own output and the command that carries on without it:

```
To update proggest.es without a copy of its databases, run: noust app backup-before-update
proggest.es off. To keep the copy, fix what the output above says and run the update again.
```

**Turning it off**, when the database is backed up another way or cannot be dumped from its
container:

```bash
noust app backup-before-update proggest.es          # shows it
noust app backup-before-update proggest.es off      # or on
```

or `backup.databases: off` in `noust.yaml`, or `PATCH /api/apps/{domain}/backup-before-update`
with `{"enabled": false}` (`apps.manage`, sudo mode), or the application's Settings > Deploys in
the console. `GET /api/apps/{domain}` shows `backup_before_update`.

**Restoring.** With the stack's service reachable through its compose file:

```bash
noust backup list proggest.es
noust backup restore BACKUP_ID --databases-only     # put back only the databases; every file stays
noust backup restore BACKUP_ID                      # files and databases, as the backup was taken
```

The application is stopped while the dumps go back and started again if it was running. A
database's container is started alone when it is not running and stopped again afterwards. The
restore replaces what the database holds (`--clean --if-exists`, `--drop`), and Postgres does it
in one transaction, so a dump it cannot read whole leaves the database as it was. Do this after
going back past a migration, as [Rolling back past a migration](#rolling-back-past-a-migration)
says.

**Volumes.** `noust backup create --include-docker-volumes` now copies the volumes Docker
really has: `<project>_<key>` unless the volume names itself with `name:` (or is `external`),
read from `docker compose config`. Before, it mounted the key, which is a new, empty volume.

## Deploying by tag

An application follows a branch, as always, or the tags that match a pattern. Following tags
fits a project that deploys on a release and is what replaces a hand-written CI gate.

```bash
noust create -d shop.example.com -s git@github.com:you/shop.git --follow-tags 'v*'
noust app follow-tags shop.example.com 'v*'    # an application already deployed
noust app follow-tags shop.example.com         # what it follows
noust app follow-tags shop.example.com --off   # the branch again
noust update shop.example.com --tag v1.4.0     # one tag, now
```

- **The pattern** is a glob over tag names (`v*`, `release-*`), case-sensitive like git's.
  Only a git source can follow tags, and not together with a pinned branch (`noust app branch
  DOMAIN --unpin` first). Setting it rebuilds nothing: the next release, or `noust update
  DOMAIN`, which deploys the newest matching tag. `noust create --follow-tags` asks the remote
  first and fails at once, before anything is built, when no tag matches.
- **Tags are ordered as versions,** never as text: `v1.10.0` is newer than `v1.9.0`, and a
  release is newer than its own `-rc.1`. A tag with no version number (`latest`, `nightly`) has
  no place in the order and is not deployed.
- **Never backwards.** A tag that is not newer than what is deployed is refused:
  `noust update --tag v1.2.0` after v1.3.0 says so and points to `noust rollback`, and a webhook
  for it is ignored and recorded.
- **The webhook** of an application that follows tags deploys what the forge announces: a
  **published release** (GitHub or Gitea `release` events with the action `published` or
  `released`, not a draft and not a pre-release) and a **tag push** (GitHub, Gitea and GitLab).
  Each queues an update to exactly that tag, resolved in the repository. A push to a branch
  deploys nothing. Enable the `release` event at the forge besides the push. Ignored deliveries
  appear in `noust app webhook deliveries DOMAIN` and in the console with the reason, as
  `ignored_tag` (a draft, a pre-release, a tag the pattern does not match, not a version, already
  deployed, older than the deployed one, or already being deployed) and `ignored_branch`.

| Endpoint | Method, permission |
|---|---|
| `/api/apps/{domain}/follow-tags` | `PATCH` `apps.manage`, sudo: `{"pattern": "v*"}`, or `{"pattern": null}` to follow the branch. `GET /api/apps/{domain}` shows `follow_tags`. |

In the console: the application's Settings > General shows the tags it deploys and **Follow
tags...** changes them.

## Workers without a web port

A stack whose services publish no TCP port on the host is a worker (it fetches, processes and
sends, and nobody connects to it). There is one definition of that for the whole product: no
service publishes a TCP port. A port published at a random host port, or in a form that cannot
be read here (`${PORT}:3000`), still counts as published, so a stack is never wrongly called a
worker and loses its site.

A worker is judged by its containers, not by a port nothing listens on: it is well when every
container runs, or ran and exited with 0 (a migration, a seed), none keeps restarting, none is
dead, none exited with an error and none reports itself `unhealthy`. `noust list`, `noust
health`, `noust diagnose`, the console and the monitor all use that. `noust diagnose` shows the
state of its containers and the last lines of `docker compose logs`, and the monitor sends
`app_unreachable` and `app_recovered` for it when a container exits with an error or restarts
in a loop (see [MONITOR.md](MONITOR.md#what-it-watches)). A deployment of one skips the site and
the certificate.

**A stack WASM 1.x registered with a port.** 1.x gave every stack the default port 3000 and
wrote it a site, so a healthy worker reads as "nothing answers on its port", and its domain
answers `502`. `noust health`, `noust diagnose` and the application's page point at the fix:

```bash
noust app headless worker.example.com                  # clears the port; asks about the site
noust app headless worker.example.com --remove-site    # and removes the site Noust wrote for it
noust app headless worker.example.com --keep-site
```

Nothing is restarted. The site is removed only when you say so, Noust wrote it and the
application answers on no other name; a certificate is left as it is. It is never done on its
own. `GET /api/apps/{domain}/headless` (`apps.read`) says whether the stack is a worker with a
port recorded and whether its site can go; `POST` (`apps.manage`, sudo) does it, with
`{"remove_site": true}` to remove the site.

## Which service gets the root route

With several services that publish a port and no `noust.nginx.yaml`, `/` goes to the **service
in front**: the web service that depends on another web service and that no other web service
depends on (in Proggest, the Next.js frontend that depends on the NestJS backend). When there is
none, to the first web service in the compose file. A port given at deploy time (`--port`) wins.

Noust no longer invents `/<service>` routes for the others: a frontend routed at `/frontend`
does not work with Next.js, and a backend left at `/` was the wrong service. The other web
services are listed in a warning with how to route them, with a `noust.nginx.yaml` (see below).

Every reader of a stack's ports is one: the short form with and without an address
(`3000:3000`, `127.0.0.1:3000:3000`, `[::1]:3000:3000`), ranges (`3000-3002:3000-3002`),
protocols (`53:53/udp`, which is not a web port), the long form with `target`, `published` and
`host_ip`, and values Compose already resolved. Before, three readers each had their own, and
the one choosing the site's port failed on `127.0.0.1:3000:3000` and fell back to 3000.

## Routing with noust.nginx.yaml

A `noust.nginx.yaml` (or the `wasm.nginx.yaml` a repository wrote for WASM) at the root of the
repository gives the site routes of its own, and tunes the plain proxy site. Docker Compose
stacks and the single-process application types read it; monorepos do not. The schema is
closed and every value is checked to be exactly what its directive takes: a key nothing reads, a
bad value or a `static` path that leaves the application is reported with its field, and a file
with a problem is **not used at all** (the site proxies `/` to the root service and the log
says why), never half applied.

| Top-level key | Meaning |
|---|---|
| `routes` | The list of routes, below. |
| `max_body_size` | `client_max_body_size` for the whole server: a number with an optional `k`, `m` or `g` (`20m`). |
| `rate_limit` | A rate (`100r/s`, `10r/m`) applied to every route that proxies and has none of its own. In 3.1 it declared a zone nobody used. |
| `security_headers` | Headers added to the defaults (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`). |
| `custom_directives` | Lines added to the server block. |

A route is **one of** `port`, `static` or `return`:

| Route key | Meaning |
|---|---|
| `path` | The location path, starting with `/` (default `/`). Paths are unique. |
| `port` | Proxy to `127.0.0.1:<port>`, 1 to 65535 (a route without `port`, `static` or `return` means 3000). |
| `static` | Serve files from this directory of the application, relative to its root, no `..`: an `alias`, with no process involved. |
| `cache` | `expires` for a `static` route: `1y`, `30d`, `12h`, `max`, `off`. |
| `return` | A redirect: `{code: 301, to: /new}`; the code is 301, 302, 303, 307 or 308. |
| `name` | Name of the upstream, derived from the path when left out. |
| `websocket` | `true` adds the upgrade headers. |
| `rate_limit`, `rate_limit_burst` | This route's rate (`10r/s`) and burst (default 5). |
| `max_body_size` | `client_max_body_size` for this route only. |
| `buffering` | `false` turns `proxy_buffering` off (server-sent events, streams). |
| `timeout`, `read_timeout`, `send_timeout` | Seconds, 1 to 86400. `timeout` defaults to 60 and is the default of the other two. |
| `buffer_size`, `strip_prefix` | `proxy_buffer_size`; `strip_prefix: true` removes the route's path before proxying. |

A file where no route names a port (routes that only serve files, redirect, or carry options for
`/`) tunes the plain proxy site instead of replacing it. For example, Proggest-like:

```yaml
max_body_size: 25m
rate_limit: 50r/s
routes:
  - path: /api/
    port: 3000
    max_body_size: 100m
    read_timeout: 300
  - path: /socket.io/
    port: 3000
    websocket: true
    buffering: false
  - path: /assets/
    static: public/assets
    cache: 1y
  - path: /uaap/
    return: {code: 302, to: /uaap/dashboard}
  - path: /
    port: 3001
```

## Other things that changed for Compose

- **`noust create --force` and a directory that holds data.** A Compose stack keeps its data
  beside its code, so with the type named (`-t docker-compose`) the deployer brings a git
  checkout that is already there to the branch in place (fetch and `reset --hard`, never
  `git clean`) and keeps what git does not track, the `.env` and bind-mounted data, and says so.
  A directory with files that is not a git checkout is refused, with how to move it aside. The
  automatic type detection (the default) fetches before the Compose deployer is chosen, so name
  the type when the directory holds data. No Compose update, and nothing in an adopted stack,
  ever runs a clean checkout.
- **`.env.example` placeholders in Spanish** are never written verbatim. A variable whose example
  value starts with `genera-`, `generar-`, `tu-`, `pon-` (each also with `_`) or contains `cambiar`
  is a template, like `your-secret-key-here` always was: for a secret it is replaced by a generated
  value, in hexadecimal with the length the marker or the name says when it says one
  (`genera-clave-hex-64-caracteres` becomes 64 hex characters), and for anything else it is left
  out and named in one warning with how to give it (`--env-file`, or `noust env configure`).
  `genera-clave-hex-64-caracteres` used to end up as the key itself and keep a backend from
  starting.
- **Volume names** resolve to what Docker really created (`<project>_<key>`, or the `name:` the
  volume gives itself), so a volume backup no longer mounts an empty new one.
- **Project names.** An adopted stack passes its project with `-p` everywhere, including the unit.
- **The build sandbox and the compose file check** are unchanged: Compose builds run inside the
  Docker daemon, a new stack with `privileged` containers or the Docker socket mounted is
  refused unless the application has an exception, and an existing one is warned. See
  [releases.md](releases.md#builds-in-the-sandbox).
- **Deleting an adopted application keeps its directory** (see [above](#deleting-an-adopted-stack)).
- **Ignored webhook deliveries** carry the `ignored_tag` reason for applications that follow
  tags.

See also: [releases.md](releases.md) for releases, the health gate and rolling back,
[domains.md](domains.md) for domains and certificates, [console.md](console.md) for the console,
[api.md](api.md) for every endpoint and [security.md](security.md) for roles and sudo mode.
