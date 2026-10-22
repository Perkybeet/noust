# API changelog

What changed in the HTTP API, release by release, for people who write against it. The product's
own changes are in the `CHANGELOG-*.md` files; this one only says what a client can notice. The
promise it is held to, and how an operation is deprecated and retired, are in
[api.md](api.md#versions-and-compatibility).

Each release has up to five lists (a major release puts its breaking changes first, so a client reads
them before the rest):

- **Added**: new operations, parameters, fields and response codes. Always compatible.
- **Changed, without breaking a client**: behaviour that moved and that a client written to the
  documentation does not notice.
- **Deprecated**: an operation announced as going away, with its `Sunset` date and what replaces
  it. It keeps working until then.
- **Removed**: an operation retired after its `Sunset`.
- **Breaking changes**: anything a client written against the previous baseline could notice
  that is not a removal after notice (a security fix, an operation that never worked). Each
  entry says what, why and what a client does now. A change here also changes
  `panel/openapi.baseline.json` in the same commit (`python scripts/api_compat.py --update
  --accept-breaking`).

## 4.0.0

API version 1: the first API that has one. This section is everything that differs from 3.3.1, which
is what `panel/openapi.baseline.json` holds. The API gains 617 operations (433 in 3.3.1, 1,050 now),
loses none, and `python scripts/api_compat.py` keeps passing. Work on it was first written down under
the number 3.4.0, which was never published; everything below ships in 4.0.0.

### Breaking changes

What a client written against 3.3.1 can notice, most likely first. None removes an operation, a
parameter or a field; each changes what a call answers or how it is addressed. Where a name from
before 4.0 is still accepted, it is accepted until 5.0 and never written.

- **The console's session cookies are `noust_session` and `noust_csrf`, and the header to echo is
  `X-Noust-CSRF`.** They carried the product's previous name. `GET /api/auth/session` says which, in
  `csrf_cookie` and `csrf_header`, as it always did, so a client that follows it changes nothing. A
  script that names the cookies, or signs in with a jar it filled by hand, signs in again. The cookies
  of 3.x are never read, and a sign-in or a sign-out expires them. The header of before 4.0 is still
  accepted beside the new one, the WebSocket subprotocols of before 4.0 are accepted and answered in
  kind, and an API token with the old prefix works until it is revoked or expires; new tokens start
  with `noust_tok_`. [UPGRADING-4.0.md](UPGRADING-4.0.md#what-still-works-and-until-when) lists each
  old name and when it stops.
- **`GET /api/auth/session` no longer answers its `renamed_from_<previous name>` field.** The console used
  it to tell an operator, once, that the product had been renamed in 3.0; the notice is gone with the
  upgrade from 2.x, which 4.0 does not move. A client that read the field reads it as absent, which is
  what `false` meant.
- **An application's export is written as `"format": "noust-app"` with `noust_version`.**
  `GET /api/apps/{domain}/export` wrote the format name and the version key of the product's previous
  name. `POST /api/apps/import` reads both spellings, and the old version key stays in the schema for an
  export made before 4.0 (read, never written). A script that compares the format name, or reads the
  version key, accepts both. When a central copies an application to a server of 3.3.x, it writes the
  spelling that release reads.
- **A backup destination is tried before it is kept.** `POST /api/backup-destinations` and
  `PUT /api/backup-destinations/{name}` connect with the fields given and answer `400
  destination_unreachable`, with the storage tool's own words, when they do not; the destination is
  not saved (a change that does not connect leaves the destination as it was). `verify: false` keeps
  one that is down on purpose, to be tried later.
- **The administrator holds every permission unless the roles are split.** On a server on the standard
  profile (the default) the `admin` role now also holds the security officer's and the auditor's:
  accounts, security settings, compliance and the audit log. `GET /api/auth/session` lists more
  permissions for it than 3.3.1 did, and a route that answered `403` to an administrator now answers.
  The old matrix is back with `security.roles_split: true`, or with a framework that asks for it (ENS
  medium and high, ISO/IEC 27001, SOC 2; `security.profile: ens-medium` is ENS medium). In the same
  release a person's several accounts are no longer held to the separation of duties, and a new account
  is no longer held at the second-factor enrolment: `mfa_required` and `mfa_pending` are true only where
  a framework asks for a second factor, or the console is published or listens beyond loopback. See
  [COMPLIANCE.md](COMPLIANCE.md) and [UPGRADING-4.0.md](UPGRADING-4.0.md#what-else-changes-on-its-own).
- **An update can be refused for a backup that did not happen.** For an application with a database,
  `POST /api/jobs/update` and the calls that deploy fail the job before anything is pulled or built
  when the backup the update needs fails, instead of warning and going on
  (`noust app backup-required DOMAIN off` turns it off for one application). A Compose deploy or
  update is refused, with the holder named, when its stack publishes a port another application, a
  stream, a database or the console holds.

### Added

#### Versions

- Every route is also served at `/api/v1/...`. It is the same route as `/api/...` (same
  permission, same audit entry), not a copy; `/api/...` keeps working.
- `X-Noust-API-Version: 1` on every response under `/api`.
- `GET /api/openapi.json`: `info.version` is the API's version (`1`; it was the product's
  version, and `unversioned` in the checked-in file), `info.x-noust-version` the product's, and
  `info.x-noust-api-prefix` the versioned address.
- Deprecated operations announce themselves with `Deprecation`, `Sunset` and `Link` headers and
  `deprecated: true` plus `x-noust-deprecation` in the schema. None is deprecated yet.

#### Applications, builds and deployments

- The applications' side of the release:
  - `GET /api/apps/{domain}/env/file` (`apps.read`) is the `.env` as the text of a file, to view or
    to download (`download=true`); values are masked unless `unmask=true`, which needs an admin
    credential and sudo mode and is audited. `POST /api/apps/{domain}/env/sync` (`apps.manage`)
    brings the text of a `.env` into the variables (`prune`, `audience`, `dry_run`) and answers
    with names only.
  - `DELETE /api/apps/{domain}` takes `remove_volumes` and `remove_databases` (both off unless
    asked), next to `remove_files` and `remove_ssl`. `GET /api/apps/{domain}/deletion`
    (`apps.read`) lists what a deletion would touch: the directory, whether the stack has Docker
    volumes, and each database with whether it would be dropped and who else uses it.
  - A container application takes `command` and `entrypoint` (a list of words, `null` for the
    image's own, `[]` to clear it), and `POST /api/apps/{domain}/container/check`
    (`apps.deploy`) says whether its registry image has a newer digest.
  - The webhook of an image from a registry: `GET /api/apps/{domain}/webhook/registry`
    (`apps.read`) and `POST .../webhook/registry/reveal` (`secrets.reveal`, audited) give the
    addresses for Docker Hub and GHCR; `POST /hooks/registry/{domain}` (GHCR's
    `registry_package`, signed with the application's webhook secret) and
    `POST /hooks/registry/{domain}/{token}` (Docker Hub, which signs nothing, so the address
    carries a token derived from the secret) queue an update when a push moved the tag the
    application runs.
  - `GET /api/apps/{domain}/compose/document` (`secrets.reveal`), `POST .../compose/document/check`
    (`apps.manage`) and `PUT .../compose/document` (`apps.manage`, sudo mode) read, judge and
    keep the Compose document of a stack deployed from one; `editable` and `reason` say when a
    stack has none.
- `PATCH /api/apps/{domain}/env` takes `audience` (a name to `both`, `build` or `runtime`) for the
  variables whose audience changes, as `PUT` does; a name that is not in the file after the merge, or an
  audience that is not one of the three, is refused as `audience` before anything is written. Changing
  only the audience of a variable names it in `changed` and sends no value.
- `POST /api/apps/{domain}/deployments/{deployment_id}/rebuild` takes `no_cache` (off by default): the
  commit is built from scratch, with an empty package manager cache and no release on disk reused. A
  request with no body is unchanged.
- `POST /api/apps` takes `team`, `project` and `environment` (where the application goes; omitted, the
  team the caller works in, its default project and `production`), `compose_document` (a Compose
  document typed in, looked at first by `POST /api/compose/analyze`, which judges a document without
  running or saving it), `container` (a container application: a Dockerfile, Railpack or a registry
  image, `container.image`, which then needs no source) and, with a recipe, `recipe_database`,
  `default_backups` and `install_requirements` (install the runtime and the engines the recipe needs
  that the server lacks, which needs sudo mode and the server's permission). `GET /api/apps` takes
  `team`, `project`, `environment` and `tag` filters, and an application answers `team`, `project`,
  `environment` and `tags`.
- The deploy queue ([releases.md](releases.md#the-deploy-queue)): `GET /api/jobs/queue` (`apps.read`)
  says what is building and what waits, in order, and the limits (`deploy.max_concurrent_builds`,
  `deploy.queue_limit`). One application builds one thing at a time, an update that is already waiting
  absorbs the same request, and one the queue has no room for is `429`. A job answers `queue_position`,
  `waiting_for`, `cancellable`, `cancel_requested` and `cancel_blocked_reason`;
  `POST /api/jobs/{job_id}/cancel` answers the job's `state`, and a deployment that was cancelled has
  the status `cancelled` with `cancelled`, `cancelled_at` and `cancelled_by`.
  `POST /api/jobs/update` takes `no_cache` beside `frozen_ok`.
- `POST /api/deploy` (`apps.deploy`) deploys several applications in one call. Each is a job of its own,
  and one that has nothing new, is not found or meets a full queue is a result of its own that does not
  stop the others ([ci.md](ci.md)).
- `GET /api/deployments/{id}` answers `certificate_state` and `certificate_output`: a certificate the
  authority refuses leaves a deployment that succeeded, served over HTTP, with what the authority
  said kept on it.
- Uploads and pushes as sources ([sources.md](sources.md)): `POST /api/uploads`, `PUT` and `GET
  /api/uploads/{upload_id}` (a chunk at the wrong offset is `409` with the offset to resume from),
  `POST .../complete` and `DELETE` take an archive up and extract it; `POST /api/uploads/dockerfile`
  makes a project out of a Dockerfile that was pasted. `GET /api/push` and `POST /api/push/setup`
  (sudo mode, `server.host_access`) say whether the server takes `git push` and prepare it.
- A JavaScript monorepo ([workspaces.md](workspaces.md)): `POST /api/workspaces/inspect` reads a
  repository's workspaces and says which are applications, and `POST /api/workspaces/deploy` (sudo
  mode) queues one application per workspace named.
- Code hosts ([sources.md](sources.md)): `GET /api/deploy-keys` lists the deploy keys and the
  applications each reaches, `GET /api/deploy-keys/server` the public half of the server's own SSH key
  (what to add at the code host before an application exists), and `GET`, `POST`
  `/api/deploy-keys/hosts` and `DELETE /api/deploy-keys/hosts/{host}` the host keys pinned (sudo mode and
  `server.host_access` to pin or forget one). `/api/integrations/gitlab` and its `connections`,
  `connections/{name}/test`, `.../groups`, `.../projects`, `.../projects/{project}/branches`,
  `oauth-apps` and `oauth/authorize|callback` connect GitLab with a token or by signing in;
  `PUT /api/integrations/github/host` chooses the GitHub the App talks to (github.com, Enterprise Server
  or Cloud), and `GET /api/integrations/github` answers `host`.
- Registry accounts for private images: `GET`, `POST /api/registries`, `GET`, `PATCH`, `DELETE
  /api/registries/{name}` and `POST .../{name}/test` (`apps.read`, `apps.manage`). The password is
  written, never read.
- The export document ([delivery.md](delivery.md)) carries `mounts` (a file's text only with
  `with_secrets`), `container` (the settings of a container application, never its registry
  password or build-secret values), and, as `GET /api/apps/{domain}/export` and `POST
  /api/apps/import` now keep them, `delivery`, `source_options` and `app.build`, which the
  endpoints used to drop. A copy to another server through the central carries all of them.
- `POST /api/compose/import` takes `format` (`coolify`, the default, or `dokploy`) and, for a Dokploy
  template, `template` (its `template.toml`); the answer gains `files` (the files the template
  mounts, by path beside the document) and `secret_files` (the ones that hold a generated secret).
  A request that sends neither is unchanged.
- `POST /api/import/applications` and `POST /api/import/proposal` read CapRover too (`platform:
  caprover`; its `token` is the dashboard's password, used to sign in for the request and not kept).
  A proposal gains `storage` (the data the application kept outside its image, with the commands
  that copy it) and `database_details` (the databases it used, with the commands that dump and load
  them); a value that looks like a credential, such as a connection string with a password in it,
  is withheld from `env` whatever its variable is called.
- The runtime of an application ([runtime.md](runtime.md)): `GET /api/apps/{domain}/processes`,
  `PUT` and `DELETE /api/apps/{domain}/processes/{name}`, `POST .../processes/{name}/scale` and
  `GET .../processes/{name}/logs` (the processes that run beside the web process);
  `GET` and `PUT /api/apps/{domain}/replicas` (copies of the web process) and `GET` and
  `PUT /api/apps/{domain}/hardening` (the systemd sandbox of the unit): each `PUT` needs sudo
  mode and answers `202` with a job, because it restarts the application behind the health
  gate; `GET`, `PUT` and `DELETE /api/apps/{domain}/mounts` and
  `GET .../mounts/content` (files kept for the application, which needs `secrets.reveal`);
  `GET /api/apps/{domain}/pending` (changes not applied yet, never the values of secrets).
- `GET` and `PUT /api/apps/{domain}/tuning`: the limits beyond the hard ones (a soft memory limit,
  swap, CPU and disk weights, pinned CPUs, ulimits), the capabilities the process keeps and the
  devices it may open ([runtime.md](runtime.md#tuning-the-unit)). `PUT` carries the whole tuning,
  needs sudo mode and answers `202` with a job, because it restarts the application behind the
  health gate.
- `GET /api/apps/{domain}/compose/variables`: the variables a Compose stack's file asks for and
  whether the stack has each (`set`, `default`, `unset` or `missing`), never a value
  ([compose.md](compose.md#variables-the-compose-file-asks-for)). A deployment or an update of a
  stack refuses before building when the file requires a value nothing gives.
- `GET`, `PUT` and `PATCH /api/apps/{domain}/env` carry `comments` (the note kept above a variable
  in the `.env`; `PUT` and `PATCH` take them over the existing ones, an empty text takes one away),
  and a variable's value may have several lines when it holds no single quote and no control
  character but the line feed ([variables.md](variables.md#values-of-several-lines-literals-and-comments)).
- `GET /api/apps/{domain}/replication`: whether an application could run on more than one server, as the server
  it is on can tell, with what blocks it and what only warns
  ([runtime.md](runtime.md#could-it-run-on-several-servers)).
- `GET /api/apps/{domain}/disk`, `PUT /api/apps/{domain}/disk` and `POST /api/apps/{domain}/disk/measure`:
  what an application takes of the disk by place, the limit its deploys are held to, and whether its
  filesystem could hold a kernel quota; measuring is a job of type `disk`
  ([runtime.md](runtime.md#disk-what-an-application-takes-and-a-limit)).
- `PUT /api/apps/{domain}/disk/kernel` (sudo mode, `apps.manage`): gives the application's limit to the
  kernel as a project quota on XFS or ext4 mounted with `prjquota`, or takes it off; a job of type `disk`.
  `GET /api/apps/{domain}/disk` answers `kernel` (`applied`, `project`, `mount`, `hard_mb`, `used_bytes`),
  read from the kernel when the quota is applied.
- `GET /api/apps/native-sites`, `POST /api/apps/adopt-site` and `GET /api/apps/{domain}/adoption`:
  list the sites on the server that Noust did not deploy, register one as it is (sudo mode), and say what
  an adopted application is ([adopting.md](adopting.md)). An adopted application has the type `adopted`;
  an update, a rollback, a rebuild, an environment change or a deployer-backed operation on it answers
  400 with the reason, and deleting it removes nothing it is made of.
- `GET /api/cron/{name}/ledger`: which moments a scheduled task was due and whether each ran on
  time, late or not at all ([runtime.md](runtime.md#scheduled-tasks-what-ran-and-what-did-not)).
  The timeline's `jobs` source carries the ones that did not as `cron_missed` events.
- The files of an application, as its own account (all need `apps.terminal`; reading a file and
  every change need sudo mode):
  `GET` and `DELETE /api/apps/{domain}/files`, `GET` and `PUT .../files/content`,
  `GET .../files/download`, `PUT .../files/upload`, `POST .../files/directory` and
  `POST .../files/move`.
- `GET` and `PUT /api/config/delivery`: `max_minutes`, the longest a build may take (60 by default; 0
  is no limit), beside `max_concurrent_builds` and `queue_limit`. A build past it fails with the time
  limit as the reason; its job and deployment are `failed`, not `cancelled`.
- `GET /api/apps/{domain}/health` reads the health check with the defaults marked, and
  `PATCH /api/apps/{domain}/health` (and the response) gain `kind`, `command`, `service`,
  `method`, `scheme`, `host`, `port`, `interval`, `retries`, `start_period` and
  `effective_summary`.
- `GET /api/deployments` and `GET /api/deployments/{id}`: `rollback_method` (`instant`,
  `rebuild` or `snapshot`) says how going back to the deployment is done.
  `GET /api/deployments/{id}/log` takes `level`, `search`, `timestamps` and `sanitize`.
- `POST /api/cron` and the jobs it lists: `timeout` (seconds, 1 to 86400) and `service` (the
  service of the application's Compose stack the command runs in).
- Shared variables by scope ([variables.md](variables.md)): `GET`, `POST` and per-name `PUT`, `DELETE` and
  `usage` under `/api/teams/{team}/variables`, `/api/teams/{team}/projects/{project}/variables`,
  `.../environments/{environment}/variables` and `/api/variables/server`. A variable has `value`,
  `secret` and `audience` (`both`, `build` or `runtime`); a value may hold `{{team.NAME}}`,
  `{{project.NAME}}`, `{{environment.NAME}}`, `{{server.NAME}}`, `{{app.URL}}` or
  `{{secret:PROVIDER/PATH}}`.
- Secret managers: `GET /api/secret-providers`, `GET /api/secret-providers/kinds`,
  `PUT` and `DELETE /api/secret-providers/{name}` and `POST .../{name}/test`. A token is written,
  never read.
- `GET /api/apps/{domain}/env` answers `audience`, `references` and `predefined` besides
  `variables`; `PUT` takes `audience`; `POST /api/apps/{domain}/env/resolve` resolves the
  references again without a deploy.
- Previews: `POST /api/apps/{domain}/previews` (a preview by hand from a `pull_request`, a
  `branch` or a `tag`), `GET .../previews/pull-requests`, `GET .../previews/{number}` and
  `.../logs`, `POST .../{number}/redeploy`, `/stop` and `/start`, and the variables defined for
  previews under `.../previews/variables`. The settings take and answer `inherit_env`, `allow_env`
  and `data_mode`; `GET .../previews` answers `has_database`, and a preview `kind`, `ref` and
  `databases`. A Docker Compose application has previews.
- `GET /api/apps/{domain}/previews/dns`: whether the names of an application's previews reach this
  server, and the record to add when they do not.
- Previews: the settings take and answer `domain_template`, `noindex` and `fork_policy`, and
  `data_mode` may be `clone`; a preview may have the status `awaiting` (a fork's commit that waits
  for an operator), and `POST .../previews/{number}/approve` and `.../reject` decide it.
- Environments: `GET /api/apps/{domain}/promotion` and `POST .../promote` put a release on the same
  application in another environment of its project, without building it again;
  `POST /api/apps/{domain}/clone` makes another application with this one's settings.
- Tags on applications: `GET` and `PUT /api/apps/{domain}/tags`, `GET /api/tags`,
  `POST /api/tags/{tag}/deploy`, and a `tags` field and a `tag` filter on the applications list.
- Holding, freezing and scheduling deploys ([delivery.md](delivery.md)):
  `GET` and `PUT /api/apps/{domain}/rollout` (the deploy history kept, the stop policy, whether a
  rollback pauses), `POST .../rollout/pause` and `.../rollout/resume`; `GET` and `POST
  /api/deploy-freezes` and `DELETE /api/deploy-freezes/{id}`; `GET`, `PUT` and `DELETE
  /api/apps/{domain}/update-schedule`; `POST .../releases/stage` (build without serving) and
  `POST .../releases/{id}/promote` (make it serve), both jobs. A release lists `waiting`.
  A deploy during a freeze, or by a pipeline during a pause, answers `409` with the code
  `deploys_frozen` or `deploys_paused`; `UpdateRequest` and the deploy options take `frozen_ok`.
- `POST /api/jobs/update-many` takes `tags`: every application that carries any of them, narrowed
  by `team`, `project` or `environment` when those are given too. `POST /api/tags/{tag}/deploy`
  is now this same call with one tag, so it also skips what the caller may not deploy where it
  lives (it queued it before).
- `POST /api/manifest/apply` (sudo mode, `apps.manage`): applies a manifest as a job made of the
  caller's own API calls, refused with `409 plan_changed` when the `expect` digest of the preview
  is not the plan's now, and with `409 apply_running` while another is being applied. The preview
  (`POST /api/manifest/plan`) answers the plan's `digest`. The job's type is `manifest`; it runs
  beside the deploy queue instead of in it.
- `GET /api/map` (`apps.read`) reads how the server fits together: the names that reach each
  application, the applications with their state, the databases, the scheduled work and the data they
  keep. It is computed on every call and carries no coordinates.
- `GET /api/environments` and `GET /api/projects` list every environment and project the principal
  sees, whatever the team.
- `GET /api/domains/temporary` (`apps.read`) works out the temporary name of this server, or of an
  application with `prefix`, `source` or `image` (`shop.203-0-113-10.sslip.io`), which resolves to the
  server's public address so that a certificate can be ordered. On a server with no public address it
  answers `200` with `available: false`, a `reason` and a `hint`; an available answer is as it was.

#### Recipes

- The catalogue ([recipes.md](recipes.md)): `GET /api/recipes` takes `q`, `category`, `kind`, `origin` and
  `tag`, and answers `total` and `problems`; `GET /api/recipes/{name}` answers the recipe in full (its
  version, images, variables, `public_ports`, `database_choices`, `build`, `license`, `changelog`).
  `GET /api/recipes/categories`, `GET /api/recipes/{name}/logo` and
  `GET /api/recipes/{name}/requirements` (what the recipe needs from this server and whether it has it,
  changing nothing) read it; `POST /api/recipes/{name}/install` (`apps.manage`) queues the install, and
  a required variable nobody gave is a `400` naming every missing one before any job exists.
- Installed recipes: `GET /api/recipes/installed`, `GET /api/recipes/installed/{domain}` and
  `POST /api/recipes/installed/{domain}/update` (a job that goes through the one update, with its backup,
  health gate and way back).
- The server's own recipes and signed packages (`server.manage`; what changes needs sudo mode): `POST /api/recipes/private`
  and `.../check`, `DELETE /api/recipes/private/{name}`, `GET /api/recipes/catalog`,
  `POST /api/recipes/catalog/fetch` and `DELETE /api/recipes/catalog`.

#### Databases

- `/api/databases/instances`: the engines Noust runs itself, in containers, beside the host's
  (another version of an engine, or one the host does not provide): list, `catalog`, `plan`, make
  (a job, sudo mode), describe, `start` (a job), `stop`, `upgrade` (a job, sudo mode),
  `remove-plan` and remove (`confirm`, a job, sudo mode). An instance is addressed everywhere an
  `{engine}` is taken by its key (`postgresql@pg16`).
- `GET /api/databases/engines`: `managed`, `pinned_version` and `memory_limit_mb` on an engine that
  is an instance Noust runs.
- `GET /api/databases/provisioning/choices`: each flavour lists its `instances` and the
  `instance_versions` one could be made in; a version an instance runs has `state` `instance` and
  the `instance` that runs it. `GET .../provisioning/plan` takes `instance`, and answers with it.
- `POST /api/apps/{domain}/databases` and `POST /api/apps` (`database`): `instance`, the name of
  an instance to make the database on. A `version` the host does not run is answered by the
  instance that runs it, when exactly one does.
- Point-in-time recovery of the host's PostgreSQL ([databases.md](databases.md#recovering-postgresql-to-an-instant)):
  `GET /api/databases/pitr` (the window that can be recovered, the base backups, the archiver, the
  timer, warnings), `POST .../pitr/enable/plan` and `.../pitr/restore/plan` (read-only), and, as jobs
  with sudo mode, `POST .../pitr/enable`, `.../disable`, `.../base`, `.../sync`, `.../fetch` and
  `.../restore`. A change that restarts the engine or replaces the cluster is refused without
  `confirm`. The reads need `databases.read`, the jobs `databases.manage`.
- Importing ([databases.md](databases.md)): `POST /api/databases/imports/inspect` says what a dump is and
  whether an engine can load it; `POST /api/databases/imports` loads it, `POST /api/databases/imports/cluster`
  (and `.../cluster/plan`) loads a dump of every database, with their accounts when asked; the dump comes
  up in chunks that resume (`POST|GET|PUT|DELETE /api/databases/imports/uploads...`). They need
  `databases.manage`, and loading needs sudo mode. A plain dump is loaded as the database's owner, never
  as the superuser, unless the request asks.
- `POST /api/databases/moves` (and `.../plan`) moves a database to another engine, with the way back;
  `POST /api/fleet/databases/transfer` (`fleet.manage`) moves one to another server of the fleet, and
  `GET /api/databases/backups/{name}/data` reads a range of a dump so that a central can carry it
  (sudo mode; the read is audited where it begins).
- Reachable from the network: `GET /api/databases/public`, `GET /api/databases/public/{engine}/{database}`,
  `POST .../public` (and `.../public/plan`; without `confirm` the answer is a `400 confirmation_required`
  carrying every warning), `.../reveal` (audited as a password shown) and `DELETE`.
- Replicas and the TLS of the host's engines: `GET /api/databases/replicas[/{engine}]`,
  `POST .../replicas/{engine}` (and `/plan`) prepares a primary and answers the join code once,
  `POST .../replicas/join` (and `/plan`) makes this server a replica, `.../promote`, `.../{name}/token`
  and `DELETE .../replicas/{engine}/{name}`; they need `databases.manage` and sudo mode, and the plans
  change nothing.
- Extensions and tags: `GET`, `POST /api/databases/databases/{engine}/{name}/extensions` and `DELETE
  .../extensions/{extension}`; `GET`, `PUT .../tags` on a database and on a service
  (`/api/services/{name}/tags`). `GET /api/databases/databases`, `POST` and the database itself carry
  `team`, `project`, `environment` and `tags`, and the list takes the same filters. A link to a database
  takes `tls` and the detected links answer `tls_mode`.

#### Backups and recovery

- Data and volumes ([disaster-recovery.md](disaster-recovery.md#application-data)): `/api/volume-backups` lists, takes
  (`POST`), reads, verifies (`POST .../{backup_id}/verify`), pushes to a destination (`.../push`), restores
  (`.../restore`, sudo mode; the application is stopped, a safety copy taken, and the data put back if
  the health gate fails) and deletes data backups; `.../policies` and `.../policies/{domain}` hold an
  application's policy and `POST .../policies/{domain}/run` runs it; `.../targets/{domain}` lists the data
  an application declares, `.../remote/{domain}` what a destination holds, and `POST .../restore-remote`
  restores from there.
- Noust's own state ([disaster-recovery.md](disaster-recovery.md#nousts-own-state)): `/api/self-backup` lists the bundles and the schedule, `POST /api/self-backup`
  and `.../run` take one, `PUT` and `DELETE .../schedule` set the schedule, and `POST
  .../{name}/verify` and `.../{name}/push` prove a bundle and send it to a destination, `DELETE
  .../{name}` removes it.
- Encryption: `GET /api/backups/encryption` (is it on, with which key, how many are sealed),
  `POST .../enable`, `.../disable`, `.../rotate`, `.../keys` (put a lost key back),
  `GET .../keys/{key_id}` (`secrets.reveal`), `GET` and `POST .../seal-existing` (count, then seal what was
  made in clear), and `POST /api/backup-destinations/protect`, which seals the credentials that destinations
  still keep in clear. `POST /api/backup-destinations` and `PUT .../{name}` take `verify`.
- Failures: `GET /api/backups/failures`, `GET .../failures/{failure_id}` and `.../output` keep and
  answer everything the tool said, plain.
- A whole server: `GET /api/server/restore/plan` and `POST /api/server/restore` (sudo mode) bring a server
  back application by application, `GET /api/server/migration/plan`, `POST .../migration/prepare` and
  `GET .../migration/check` prepare a move to another server and check it once it has happened.
- Schedules and policies: `POST` and `PUT /api/backup-schedules` take `max_runtime_seconds`,
  `remote_only`, `retention_gb` and `timezone`; a database's policy
  (`/api/databases/backup-policies/{engine}/{database}`) takes the same and `exclude_databases`,
  `exclude_schemas` and `exclude_tables`, and answers `exclusions`, `scope`, `remote_only` and `retention_gb`.

#### Domains, certificates and the web server

- `PUT /api/apps/{domain}/domains/{name}/enabled` (`{"enabled": false}`) switches a name off without
  deleting it, or serves it again; `enabled` in every entry of `GET|POST /api/apps/{domain}/domains`.
- `force_https` in the policy of an application (`GET|PUT /api/apps/{domain}/policy`).
- The DNS check's `cdn` is also `fastly`, `bunnycdn` or `arvancloud`.
- A site's policy ([network.md](network.md)): `GET`, `PUT` and `DELETE /api/apps/{domain}/policy`,
  `PUT` and `DELETE /api/apps/{domain}/policy/users/{username}`, and the server's
  `GET /api/policy/cloudflare-ranges` with `POST .../refresh`. A list that would refuse the caller's
  own address is a `400` on `allow_ips` until `accept_lockout` is sent.
- Domains: `port` on `GET /api/apps/{domain}/domains` and on its entries, `canonical` on the list and on
  every change, `force`, `port` and `dns` in the body of `POST /api/apps/{domain}/domains`, `dns` and
  `canonical` on every change, `PUT /api/apps/{domain}/domains/canonical` and
  `PUT /api/apps/{domain}/domains/{name}/port`. A wildcard (`*.example.com`) is a valid alias. Removing a
  name queues the certificate to be ordered again without it (`certificate_job_id`). A name another site
  already answers on is a `409` with `error: domain_overlap`. `verdict`, `cdn` and `resolver` on
  the DNS check (`here`, `cdn`, `elsewhere`, `none`, `unknown`).
- DNS: `/api/dns/providers` (list with the kinds, add and `PATCH`, `DELETE`, `.../verify`),
  `/api/dns/records`, `POST /api/dns/sweep` and `GET`, `POST /api/apps/{domain}/dns`. A record somebody
  else made at a name is a `409` with `error: record_conflict` and `hostname`, `record_id`, `current` and
  `wanted`; the same request with `replace_record` and `replace_current` replaces exactly that record.
- Certificates: `GET`, `PUT /api/certs/acme` and `POST /api/certs/acme/register` (a job),
  `GET`, `POST /api/certs/uploaded` and `DELETE /api/certs/uploaded/{name}`,
  `GET`, `PUT /api/apps/{domain}/certificate`. `source` and `challenge` on the entries of
  `GET /api/certs`, and `auto_renew` is false for an upload.
- `GET`, `POST /api/streams`, `POST /api/streams/plan` and `DELETE /api/streams/{name}`: the TCP and UDP
  ports nginx publishes. `409` when nginx cannot (`error: prerequisiteerror`, with the fix in `hint`).
- `GET`, `POST /api/tunnels`, `DELETE /api/tunnels/{name}`, `POST /api/tunnels/{name}/routes` and
  `DELETE /api/tunnels/{name}/routes/{hostname}`: Cloudflare Tunnels. A conflicting DNS record is an
  outcome of the route (`dns.outcome: conflict`), not an error.
- `/api/certs/ca`: the server's own certificate authority for names that only resolve inside a private
  network. `GET` (the authority, the certificates it signed, whether this server trusts it), `POST`
  (make it; sudo mode, `settings.manage`) and `DELETE` (`force`; sudo mode); `GET .../root?format=pem|der`
  (the public certificate), `GET .../trust` (how to trust it on each kind of device, with the
  fingerprint), `POST|DELETE .../trust-here` (this server's own store; sudo mode), `POST .../issued`
  and `POST .../issued/{name}/renew` (`apps.manage`). A second authority is `409`, and a server
  without one answers `404` (`not_found`).
- `PUT /api/apps/{domain}/certificate` takes `source: "internal"` (and `days`): the server's own
  authority signs a certificate for every name the application answers on and it is served at once.
- A site and what answers around it: `GET /api/sites/{domain}/config/versions` and `.../{version}` keep
  every version of a site's file with a diff, and `POST .../{version}/restore` puts one back (sudo mode,
  tested by the web server before it is installed); `GET /api/sites/{domain}` answers `edited_outside` and
  `pending_reload`. `GET`, `PUT`, `DELETE /api/unknown-hosts` choose what answers a name no site serves
  (sudo mode). `GET /api/private-networks`, `GET /api/policy/forward-auth-presets` (oauth2-proxy,
  authentik and Authelia) and `POST /api/waf/install` (ModSecurity and the Core Rule Set, from the
  distribution, sudo mode). `POST /api/server/security/fail2ban/web` makes fail2ban watch nginx, and
  `GET /api/server/security/fail2ban` answers `web_available` and `web_jails`.
- A certificate for one name: `POST /api/apps/{domain}/domains` takes `certificate` (`shared`, the
  default, or `own`), `challenge` (`http-01` or `dns-01`) and `dns_provider`;
  `PUT /api/apps/{domain}/domains/{name}/certificate` gives a name a certificate of its own or takes it
  back, and `GET /api/certs/{domain}` answers `source`, `challenge` and `serves` (which names the
  certificate presents) ([certificates.md](certificates.md)).

#### Fleet

- `POST /api/fleet/placements/{domain}/rollout` follows the application's build server: with one
  recorded it is planned and run as the shipment (`.../ship`) and the plan's `action` says
  `placement_ship`; `build_on_each` builds on every server instead, and a git tag, a registry image
  tag or a build server that is itself a member are rolled out as before, with the reason in the
  plan's notes. Its options gain `build_on_each`. An application without a build server is
  unchanged.
- `POST /api/nodes/{node}/ssh/authorization` (`fleet.manage`): the line a bastion's operator adds,
  asked for with the bastion the operator means to set, before setting it, as `noust node ssh NAME
  --bastion ... --show` does. Setting a bastion opens the tunnel through it, which cannot work until
  the bastion lets the central in; `GET .../ssh` gave the line only once the bastion was set.
- A release built on one server and run on others (a central's build server):
  `POST /api/apps/{domain}/releases/bundle` (a job) builds without activating and leaves the bundle in
  a spool; `GET .../releases/bundles`, `GET .../releases/bundles/{bundle_id}`, `GET
  .../releases/bundles/{bundle_id}/data?offset=&length=` (raw bytes) and `DELETE
  .../releases/bundles/{bundle_id}` read and let go of it; `POST .../releases/uploads`, `GET`/`PUT
  .../releases/uploads/{upload_id}` (`offset` query), `POST .../uploads/{upload_id}/complete` and
  `DELETE` take one in, with the digest declared up front; `POST .../releases/receive` (a job, held
  by a freeze of deploys, `frozen_ok`) activates it behind the health gate. All are `apps.deploy`.
- `GET`, `PUT` and `DELETE /api/fleet/builders[/{domain}]`: the server that builds an application for
  the others (`fleet.read` and `fleet.manage`), and `POST /api/fleet/placements/{domain}/ship`
  (`fleet.manage`, planned and queued like a rollout) builds on it and runs that build on the
  placement's servers.
- An application on several servers ([CENTRAL.md](CENTRAL.md#an-application-on-several-servers)):
  `GET /api/fleet/placements`, `GET`, `PUT` and `DELETE /api/fleet/placements/{domain}` (the servers
  compared, and the record of where it runs), `POST .../rollout` and `POST .../copy` (fleet jobs, with
  `plan: true` to see the plan first) and `GET .../upstreams` (the servers written for a load balancer).
  A rollout takes `commit`, `tag` or `image_tag`.
- How the central connects to a server: `GET` and `PUT /api/nodes/{node}/ssh` (the wait for the
  connection and a bastion; `PUT` needs sudo mode and is tried before it is kept) and the node's
  inventory, `GET` and `POST /api/nodes/{node}/inventory`, to move a server to another central.
- `POST` and `DELETE /api/fleet/cloud/servers/{node}/link`: link a server to its machine at a
  provider, or forget it (sudo mode).
- Servers made at a cloud provider (`/api/fleet/cloud`, [CENTRAL.md](CENTRAL.md#creating-a-server-at-a-cloud-provider)):
  `providers`, `accounts` (add, replace the token, verify, remove), an account's `catalog`, the cloud-init
  library (`scripts`, `scripts/preview`) and `servers` (list, create as a job, describe, delete, `power-on`).
  Tokens are in a request body once and in no response.
- Servers behind NAT (`/api/fleet/link`, [CENTRAL.md](CENTRAL.md#servers-behind-nat-or-a-cgnat)): the status,
  `invites` (create, cancel) and `accept`. `GET /api/nodes` and `GET /api/nodes/{node}` answer `link` for a
  server that dials in, and its `tunnel` carries `transport: "link"`.
- `PUT /api/nodes/{node}/monitoring` (pause or resume a server's monitoring), and `monitoring` on a node: how
  long it has been silent and whether it is paused.
- `POST /api/fleet/actions`: `noust_update` takes the option `target_version`, and `index_refresh`
  ("Refresh package lists") is an action. A plan's server step says "Noust X -> Y", and a skipped server's
  reason may be `on_the_way` or `index_behind`.
- `GET /api/fleet/metrics`: on a central, one metric of every server over the same window, from each server's own
  history (`fleet.read`).
- Guided enrollment: `POST /api/fleet/enroll/host-key` asks an address for its ed25519 host key, to be
  confirmed before anything is sent, and `POST /api/fleet/enroll` (`fleet.manage`, sudo mode) installs
  Noust if needed, authorizes this central and joins the server. The credential is used once and kept
  nowhere. A node answers `labels`, `link`, `monitoring` and `team`.

#### Access, teams and signing in

- Single sign-on through an OpenID Connect provider ([sso.md](sso.md)): `GET /api/auth/oidc/providers`,
  `GET /api/auth/oidc/start`, `GET /api/auth/oidc/callback` and `POST /api/auth/oidc/complete` (public,
  counted by the sign-in lockout), and the security officer's `/api/auth/oidc/config`,
  `/api/auth/oidc/config/{provider}` (`PUT`, `DELETE`), `/api/auth/oidc/config/{provider}/test` and
  `/api/auth/oidc/identities`. `POST /api/auth/login/second-factor` also finishes the step a provider's
  answer opens, and `auth_method` of a session may now be `oidc`.
- Single sign-on through a SAML 2.0 provider ([sso.md](sso.md#saml-20)): `GET /api/auth/saml/start`,
  `POST /api/auth/saml/acs` (a form, `SAMLResponse` and `RelayState`) and
  `GET /api/auth/saml/metadata/{provider}` (public; the first two are counted by the sign-in lockout),
  and the security officer's `/api/auth/saml/config`, `/api/auth/saml/config/{provider}` (`PUT`,
  `DELETE`) and `/api/auth/saml/config/{provider}/test`. `GET /api/auth/oidc/providers` lists them with
  `protocol: "saml"`, a button that starts at `/api/auth/saml/start`; the ticket is collected at
  `POST /api/auth/oidc/complete` like any other, and `auth_method` of a session may be `saml`.
- Sign-in against a directory ([sso.md](sso.md#directories-ldap-active-directory)):
  `POST /api/auth/ldap/login` (public, counted by the sign-in lockout) and the security officer's
  `/api/auth/ldap/directories`, `/api/auth/ldap/directories/{name}` (`PUT`, `DELETE`) and
  `/api/auth/ldap/directories/{name}/test`. `GET /api/auth/oidc/providers` lists them with
  `protocol: "ldap"` and gains the `protocol` field on every entry.
- SCIM 2.0 provisioning ([sso.md](sso.md#provisioning-scim)): `/api/scim/v2` (`ServiceProviderConfig`,
  `ResourceTypes`, `Schemas`, `Users`, `Groups`), authenticated by the bearer token of a source, and the
  security officer's `/api/auth/scim/sources`, `/api/auth/scim/sources/{name}` (`PUT`, `DELETE`) and
  `/api/auth/scim/sources/{name}/users`.
- `POST /api/auth/oidc/identities` links a person at a SAML provider or a directory as well, by the
  name of the source. Group mappings take `bind`, `project` and `environment` (a role held at a project
  or an environment) beside `role`, `team` and `team_role`.
- `PATCH /api/auth/tokens/{token_id}` changes where a token is accepted from (`allowed_cidrs`); adding
  addresses or lifting the limit needs sudo mode. A token gets `403 api_disabled` while
  `web.api_enabled` is `false`.
- Teams, projects and environments ([teams.md](teams.md)): `/api/teams` (list, create), `/api/teams/{team}`
  (read, rename, delete), `.../members`, `.../access` (a role given on the team, a project or an
  environment), `.../projects` and `.../projects/{project}/environments` (create, rename, delete),
  `.../environments/{environment}/variables[/{name}]` with `.../usage`, and `.../notifications/channels`
  (a team's own channels, with `POST .../{name}/test`). `GET /api/teams/mine`, `PUT /api/teams/active` and
  `GET /api/teams/roles` say which teams the caller belongs to, choose the one it works in and describe
  what a team role may do. A resource outside the caller's teams is a `404`, never a `403`.
- Roles of your own: `GET`, `POST /api/access/roles`, `GET`, `PATCH`, `DELETE /api/access/roles/{role}` and
  `GET`, `POST /api/access/bindings`, `DELETE .../{binding_id}` (a role at any scope, the whole server
  included). `POST /api/auth/tokens` takes `team`, `project` and `environment` and answers `bound`.
- Accounts: an account answers and takes `email`; `POST /api/auth/invitations` takes `email`, `team` and
  `team_role`, mails the link when the server has a mail channel (`emailed`, `email_error`), and
  `POST /api/auth/password-reset/request` and `.../confirm` (public) choose a new password with a mailed
  link and a code of the second factor. `GET`, `PUT /api/auth/oidc/local-login` say and change whether
  passwords and passkeys still sign accounts in (sudo mode; refused while no administrator could sign in).
- Compliance ([COMPLIANCE.md](COMPLIANCE.md)): `GET /api/compliance` (the frameworks on, the controls they
  force), `POST /api/compliance/plan` (what a change would do, writing nothing), `PUT /api/compliance`
  (turning a framework off or loosening a control needs sudo mode, and a change with warnings is made only
  once `acknowledge` is true) and `GET /api/compliance/check` (framework by framework, with the clause of
  each finding).
- A terminal ([terminal.md](terminal.md)): `GET /api/terminal/status`, `POST /api/server/terminal` (a
  ticket for a root shell, `server.terminal`, sudo mode, off until an administrator turns it on),
  `GET /api/terminal/sessions`, `DELETE .../sessions/{session_id}`, and the recordings kept in the audit
  (`GET /api/terminal/recordings[/{recording_id}]`, `audit.read`).
- MCP ([mcp.md](mcp.md)): `POST /api/mcp` answers one MCP message or a batch under the caller's own
  permissions, `GET /api/mcp/status` says whether it is on, where to connect and what it offers, and
  `PUT /api/mcp/settings` (`security.manage`, sudo mode) turns it on or off and chooses the groups of
  tools. `GET` and `DELETE /api/mcp` answer that the server keeps no stream or session between calls.
- The console on a name of its own ([console.md](console.md)): `GET /api/console/exposure`, `POST
  /api/console/exposure/preview`, `POST /api/console/exposure` (a job; the checks run first and a refusal is
  the response) and `DELETE /api/console/exposure` (`server.host_access`, sudo mode).

#### Logs, metrics and notifications

- `latency_p50_ms`, `latency_p95_ms` and `visitors` in the totals and the points of
  `GET /api/apps/{domain}/traffic`; the alert metric `app_latency_p95` (unit `ms`).
- The event kinds `security_release`, `container_restart_loop` and `recipe_outdated`, and the
  `webhook_headers` setting of the generic webhook channel.
- The log-shipping kind `newrelic` (and `region`).
- Log shipping: `GET /api/log-drains`, `PUT` and `DELETE /api/log-drains/{name}` and
  `POST /api/log-drains/{name}/test` (`settings.read`, `settings.manage`). A token is only
  written: the destination reports `has_secret`, never the secret.
- Notification channels by name ([notifications.md](notifications.md)): `GET /api/notifications/channels`,
  `PUT` and `DELETE /api/notifications/channels/{name}` (sudo mode) with a channel's own settings,
  events, route and quiet hours, `GET /api/notifications/events`, `GET /api/notifications/deliveries`,
  `POST /api/notifications/deliveries/sweep` and `POST /api/notifications/deliveries/{id}/retry`.
  `POST /api/config/notifications/{channel}/test` takes the name of any channel, and the channel
  types `pushover`, `ntfy`, `gotify` and `teams` exist besides the five of 3.3; `mattermost`, `matrix`,
  `lark` and `pagerduty` are further types.
- `GET` and `PUT /api/config/email-api`: the provider the monitor's mail goes through instead of SMTP
  (`resend`, `postmark`, `brevo` or `ses`). Credentials are write-only: the answer says whether one is
  stored. `PUT` needs sudo mode.
- Alert rules: `GET /api/alerts/rules`, `PUT` and `DELETE /api/alerts/rules/{name}`, and
  `POST /api/alerts/rules/{name}/enable` and `/disable`. `POST` and `DELETE
  /api/alerts/rules/{name}/acknowledge` take a firing alert (`apps.operate`) or hand it back, and a
  state carries `acknowledged_at` and `acknowledged_by`.
- `GET /api/apps/{domain}/site-logs`: the end of a site's access or error log, masked.
- `GET /api/logs/search` and, on a central, `GET /api/fleet/logs`: one search over the logs of the applications the
  caller sees (`apps.read`, `fleet.read`). `GET`, `PUT`, `DELETE /api/logs/retention`: how long the journal keeps them
  (`server.read`, `server.manage`; sudo mode to change). See [logs.md](logs.md).
- A notification channel (`GET /api/notifications/channels`, `PUT .../{name}`) carries `flood` (at most `max`
  messages in `minutes` minutes, the rest arriving as one) and `summary` (what is below a severity waits for
  a message once a day); a delivery carries `held_for` (`quiet`, `flood` or `summary`).
- `GET /api/probes`, `PUT`, `DELETE /api/probes/{name}`, `POST /api/probes/{name}/check` and
  `GET /api/probes/{name}/history`: addresses the monitor asks from outside the application. The status page
  (`GET`, `PUT /api/probes/status-page`, `POST /api/probes/status-page/write`) and its publication on a name
  (`POST /api/probes/status-page/publish`, `DELETE /api/probes/status-page/site`, jobs). See
  [probes.md](probes.md).
- `GET /api/apps/{domain}/traffic`: an application's requests by the hour (by the day beyond two
  days), how they ended, bytes served, and its top pages, networks, status codes and referrers
  (`apps.read`). `GET /api/apps/{domain}/metrics` now names the `requests` and `errors_5xx` series of
  any application whose site has an access log, not only a static site's or a PHP application's.
- `GET /api/metrics/prometheus`: the newest value of every metric in the Prometheus text
  exposition format, or OpenMetrics when `Accept` asks for it (server `server.read`).
- `GET /api/apps/{domain}/metrics` answers `containers` for a Compose stack: each container and the
  metric names of its own CPU, memory, disk and network series.
- `GET /api/apps/{domain}/metrics`: `series` may also name `disk_read`, `disk_write`, `net_rx` and
  `net_tx`, the series of an application's disk and network traffic; `GET /api/metrics/query` serves
  them like the others.
- The notification events `cron_missed`, `cleanup_success`, `cleanup_failed`, `probe_failed` and
  `probe_recovered` (`GET /api/notifications/events` lists them).
- The notification events `alert_firing`, `alert_resolved`, `backup_missing`, `cron_failed`,
  `cron_success`, `os_patches` and `token_expiring`, and the configuration keys `monitor.disk_*`,
  `monitor.cert_warning_days`, `monitor.backup_*`, `monitor.os_patches_check_hours` and
  `monitor.token_warning_days`.
- `GET /api/server/logs/search` and `GET /api/server/logs/context` (`secrets.reveal`) answer a query over the
  journal with fields, `AND`, `OR`, `-`, quotes and groups, and show what was logged around one entry
  ([logs.md](logs.md)).
- `GET /api/apps/{domain}/logs` takes `service` and answers `service` and `services` (one service of a
  Compose stack at a time); `GET /api/deployments/{id}/log` takes `level`, `search`, `timestamps` and
  `sanitize`.

#### The server, updates and support

- `GET`/`PUT /api/server/storage/schedule`, `POST /api/server/storage/schedule/run` and
  `GET /api/server/storage/history`: the policy that cleans a server's disk by itself, and the history of
  every cleanup.
- `GET /api/system/version`: `security_update`, `security_version` and `advisory_url`. A release newer
  than the installed one that fixes a vulnerability is told apart from an ordinary update.
- `GET /api/system/health`: a `Security posture` check among `checks` (backups that leave unencrypted, a
  central's keys not sealed, a security release that is out), each also a line of `warnings`.
- `GET /api/system/update/auto` and `PUT /api/system/update/auto` (`server.manage`, sudo mode) say and change
  when, how and whether Noust installs its own new releases by itself: a window on the server's clock, the
  days it opens on and the channel (a new major version is never installed by itself).
- `GET /api/system/changelog` lists what changed in each release the installation ships with (offline),
  `GET /api/system/release-notes` reads what updating to a version will change before updating,
  `GET /api/system/doctor` runs the checks of `noust setup doctor`, and `GET /api/system/support-report`
  builds the report of `noust support report` (its journal excerpt only for a credential that may reveal
  secrets). All are `server.read`.

### Changed, without breaking a client

- `GET /events` and `/api/nodes/{node}/events` answer `429` (`"error": "rate_limited"`) to a credential
  that already holds sixteen event streams, and a stream ends after twelve hours (the browser's
  `EventSource` reconnects by itself).
- The anonymous rate limit, the sign-in lockout and the webhook budget count an IPv6 client by its /64.
- Every response carries `X-Permitted-Cross-Domain-Policies: none`, and a longer `Permissions-Policy`.
- The text of `GET /api/apps/{domain}/logs`, `GET /api/services/{name}/logs` and the log stream has
  credentials of a recognisable shape (private keys, tokens, `password=` assignments, URLs with a
  password) replaced by `***`.
- `GET /api/auth/session` and the login answers say `second_factor` and `mfa_required` per the control
  described under Breaking changes; the sign-in answers do not otherwise change.
- A new application gets the defaults of 4.0 (zero-downtime deploys once its first deployment answered, a
  unit in systemd's sandbox, a stack given no port of its own unless one is asked for); one that exists
  keeps its way until its operator changes it.
- `GET /api/services` filters on `noust_only`; the name it had before 3.0 stays as a parameter marked
  `deprecated` in the schema, read when `noust_only` is absent (until 5.0).

### Deprecated

Nothing is announced with a `Sunset` date. The old names listed under Breaking changes are accepted until
5.0 and never written; the one parameter of that kind that the schema marks `deprecated` is the old
spelling of `noust_only`.

### Removed

Nothing.

## 3.3.1 and earlier

The API had no version and no stated promise; `/api` was the only address. Version 1 starts from
what 3.3.1 served.
