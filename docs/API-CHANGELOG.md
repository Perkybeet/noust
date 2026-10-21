# API changelog

What changed in the HTTP API, release by release, for people who write against it. The product's
own changes are in the `CHANGELOG-*.md` files; this one only says what a client can notice. The
promise it is held to, and how an operation is deprecated and retired, are in
[api.md](api.md#versions-and-compatibility).

Each release has up to four lists:

- **Added**: new operations, parameters, fields and response codes. Always compatible.
- **Deprecated**: an operation announced as going away, with its `Sunset` date and what replaces
  it. It keeps working until then.
- **Removed**: an operation retired after its `Sunset`.
- **Breaking changes**: anything a client written against the previous baseline could notice
  that is not a removal after notice (a security fix, an operation that never worked). Each
  entry says what, why and what a client does now. A change here also changes
  `panel/openapi.baseline.json` in the same commit (`python scripts/api_compat.py --update
  --accept-breaking`).

## 4.0.0

### Added

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
- `POST /api/fleet/placements/{domain}/rollout` follows the application's build server: with one
  recorded it is planned and run as the shipment (`.../ship`) and the plan's `action` says
  `placement_ship`; `build_on_each` builds on every server instead, and a git tag, a registry image
  tag or a build server that is itself a member are rolled out as before, with the reason in the
  plan's notes. Its options gain `build_on_each`. An application without a build server is
  unchanged.
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
- `PUT /api/apps/{domain}/domains/{name}/enabled` (`{"enabled": false}`) switches a name off without
  deleting it, or serves it again; `enabled` in every entry of `GET|POST /api/apps/{domain}/domains`.
- `GET /api/apps/{domain}/previews/dns`: whether the names of an application's previews reach this
  server, and the record to add when they do not.
- `force_https` in the policy of an application (`GET|PUT /api/apps/{domain}/policy`).
- `latency_p50_ms`, `latency_p95_ms` and `visitors` in the totals and the points of
  `GET /api/apps/{domain}/traffic`; the alert metric `app_latency_p95` (unit `ms`).
- The event kinds `security_release`, `container_restart_loop` and `recipe_outdated`, and the
  `webhook_headers` setting of the generic webhook channel.
- The log-shipping kind `newrelic` (and `region`).
- The DNS check's `cdn` is also `fastly`, `bunnycdn` or `arvancloud`.
- `POST /api/nodes/{node}/ssh/authorization` (`fleet.manage`): the line a bastion's operator adds,
  asked for with the bastion the operator means to set, before setting it, as `noust node ssh NAME
  --bastion ... --show` does. Setting a bastion opens the tunnel through it, which cannot work until
  the bastion lets the central in; `GET .../ssh` gave the line only once the bastion was set.

## 3.4.0

API version 1.

### Added

- Every route is also served at `/api/v1/...`. It is the same route as `/api/...` (same
  permission, same audit entry), not a copy; `/api/...` keeps working.
- `X-Noust-API-Version: 1` on every response under `/api`.
- `GET /api/openapi.json`: `info.version` is the API's version (`1`; it was the product's
  version, and `unversioned` in the checked-in file), `info.x-noust-version` the product's, and
  `info.x-noust-api-prefix` the versioned address.
- Deprecated operations announce themselves with `Deprecation`, `Sunset` and `Link` headers and
  `deprecated: true` plus `x-noust-deprecation` in the schema. None is deprecated yet.
- `/api/databases/instances`: the engines Noust runs itself, in containers, beside the host's
  (another version of an engine, or one the host does not provide): list, `catalog`, `plan`, make
  (a job, sudo mode), describe, `start` (a job), `stop`, `upgrade` (a job, sudo mode),
  `remove-plan` and remove (`confirm`, a job, sudo mode). An instance is addressed everywhere an
  `{engine}` is taken by its key (`postgresql@pg16`).
- `GET /api/databases/engines`: `managed`, `pinned_version` and `memory_limit_mb` on an engine that
  is an instance Noust runs.
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
- `GET /api/databases/provisioning/choices`: each flavour lists its `instances` and the
  `instance_versions` one could be made in; a version an instance runs has `state` `instance` and
  the `instance` that runs it. `GET .../provisioning/plan` takes `instance`, and answers with it.
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
- Log shipping: `GET /api/log-drains`, `PUT` and `DELETE /api/log-drains/{name}` and
  `POST /api/log-drains/{name}/test` (`settings.read`, `settings.manage`). A token is only
  written: the destination reports `has_secret`, never the secret.
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
- `POST /api/apps/{domain}/databases` and `POST /api/apps` (`database`): `instance`, the name of
  an instance to make the database on. A `version` the host does not run is answered by the
  instance that runs it, when exactly one does.
- Servers made at a cloud provider (`/api/fleet/cloud`, [CENTRAL.md](CENTRAL.md#creating-a-server-at-a-cloud-provider)):
  `providers`, `accounts` (add, replace the token, verify, remove), an account's `catalog`, the cloud-init
  library (`scripts`, `scripts/preview`) and `servers` (list, create as a job, describe, delete, `power-on`).
  Tokens are in a request body once and in no response.
- Servers behind NAT (`/api/fleet/link`, [CENTRAL.md](CENTRAL.md#servers-behind-nat-or-a-cgnat)): the status,
  `invites` (create, cancel) and `accept`. `GET /api/nodes` and `GET /api/nodes/{node}` answer `link` for a
  server that dials in, and its `tunnel` carries `transport: "link"`.
- `PUT /api/nodes/{node}/monitoring` (pause or resume a server's monitoring), and `monitoring` on a node: how
  long it has been silent and whether it is paused.
- `GET`/`PUT /api/server/storage/schedule`, `POST /api/server/storage/schedule/run` and
  `GET /api/server/storage/history`: the policy that cleans a server's disk by itself, and the history of
  every cleanup.
- `POST /api/fleet/actions`: `noust_update` takes the option `target_version`, and `index_refresh`
  ("Refresh package lists") is an action. A plan's server step says "Noust X -> Y", and a skipped server's
  reason may be `on_the_way` or `index_behind`.
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
- `GET /api/fleet/metrics`: on a central, one metric of every server over the same window, from each server's own
  history (`fleet.read`).
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
- The text of `GET /api/apps/{domain}/logs`, `GET /api/services/{name}/logs` and the log stream has
  credentials of a recognisable shape (private keys, tokens, `password=` assignments, URLs with a
  password) replaced by `***`.

- `GET /api/system/version`: `security_update`, `security_version` and `advisory_url`. A release newer
  than the installed one that fixes a vulnerability is told apart from an ordinary update.
- `GET /api/system/health`: a `Security posture` check among `checks` (backups that leave unencrypted, a
  central's keys not sealed, a security release that is out), each also a line of `warnings`.
- Point-in-time recovery of the host's PostgreSQL ([databases.md](databases.md#recovering-postgresql-to-an-instant)):
  `GET /api/databases/pitr` (the window that can be recovered, the base backups, the archiver, the
  timer, warnings), `POST .../pitr/enable/plan` and `.../pitr/restore/plan` (read-only), and, as jobs
  with sudo mode, `POST .../pitr/enable`, `.../disable`, `.../base`, `.../sync`, `.../fetch` and
  `.../restore`. A change that restarts the engine or replaces the cluster is refused without
  `confirm`. The reads need `databases.read`, the jobs `databases.manage`.

### Changed, without breaking a client

- `GET /events` and `/api/nodes/{node}/events` answer `429` (`"error": "rate_limited"`) to a credential
  that already holds sixteen event streams, and a stream ends after twelve hours (the browser's
  `EventSource` reconnects by itself).
- The anonymous rate limit, the sign-in lockout and the webhook budget count an IPv6 client by its /64.
- Every response carries `X-Permitted-Cross-Domain-Policies: none`, and a longer `Permissions-Policy`.
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
- Previews: the settings take and answer `domain_template`, `noindex` and `fork_policy`, and
  `data_mode` may be `clone`; a preview may have the status `awaiting` (a fork's commit that waits
  for an operator), and `POST .../previews/{number}/approve` and `.../reject` decide it.
- `POST /api/manifest/apply` (sudo mode, `apps.manage`): applies a manifest as a job made of the
  caller's own API calls, refused with `409 plan_changed` when the `expect` digest of the preview
  is not the plan's now, and with `409 apply_running` while another is being applied. The preview
  (`POST /api/manifest/plan`) answers the plan's `digest`. The job's type is `manifest`; it runs
  beside the deploy queue instead of in it.
- `POST /api/jobs/update-many` takes `tags`: every application that carries any of them, narrowed
  by `team`, `project` or `environment` when those are given too. `POST /api/tags/{tag}/deploy`
  is now this same call with one tag, so it also skips what the caller may not deploy where it
  lives (it queued it before).
- `/api/certs/ca`: the server's own certificate authority for names that only resolve inside a private
  network. `GET` (the authority, the certificates it signed, whether this server trusts it), `POST`
  (make it; sudo mode, `settings.manage`) and `DELETE` (`force`; sudo mode); `GET .../root?format=pem|der`
  (the public certificate), `GET .../trust` (how to trust it on each kind of device, with the
  fingerprint), `POST|DELETE .../trust-here` (this server's own store; sudo mode), `POST .../issued`
  and `POST .../issued/{name}/renew` (`apps.manage`). A second authority is `409`, and a server
  without one answers `404` (`not_found`).
- `PUT /api/apps/{domain}/certificate` takes `source: "internal"` (and `days`): the server's own
  authority signs a certificate for every name the application answers on and it is served at once.

### Deprecated

Nothing.

### Removed

Nothing.

### Breaking changes

The baseline for version 1 is the API as 3.3.1 shipped it. What differs from it:

- `GET /api/auth/session` no longer answers its `renamed_from_<previous name>` field. The console used it to tell an
  operator, once, that the product had been renamed in 3.0; the notice is gone with the upgrade
  from 2.x, which 4.0 does not move. A client that read the field reads it as absent, which is
  what `false` meant.

## 3.3.1 and earlier

The API had no version and no stated promise; `/api` was the only address. Version 1 starts from
what 3.3.1 served.
