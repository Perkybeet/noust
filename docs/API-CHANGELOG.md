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
- The files of an application, as its own account (all need `apps.terminal`; reading a file and
  every change need sudo mode):
  `GET` and `DELETE /api/apps/{domain}/files`, `GET` and `PUT .../files/content`,
  `GET .../files/download`, `PUT .../files/upload`, `POST .../files/directory` and
  `POST .../files/move`.
- Log shipping: `GET /api/log-drains`, `PUT` and `DELETE /api/log-drains/{name}` and
  `POST /api/log-drains/{name}/test` (`settings.read`, `settings.manage`). A token is only
  written: the destination reports `has_secret`, never the secret.
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
  types `pushover`, `ntfy`, `gotify` and `teams` exist besides the five of 3.3.
- Alert rules: `GET /api/alerts/rules`, `PUT` and `DELETE /api/alerts/rules/{name}`, and
  `POST /api/alerts/rules/{name}/enable` and `/disable`.
- `GET /api/apps/{domain}/site-logs`: the end of a site's access or error log, masked.
- `GET /api/apps/{domain}/metrics`: `series` may also name `disk_read`, `disk_write`, `net_rx` and
  `net_tx`, the series of an application's disk and network traffic; `GET /api/metrics/query` serves
  them like the others.
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
- `POST /api/manifest/apply` (sudo mode, `apps.manage`): applies a manifest as a job made of the
  caller's own API calls, refused with `409 plan_changed` when the `expect` digest of the preview
  is not the plan's now, and with `409 apply_running` while another is being applied. The preview
  (`POST /api/manifest/plan`) answers the plan's `digest`. The job's type is `manifest`; it runs
  beside the deploy queue instead of in it.
- `POST /api/jobs/update-many` takes `tags`: every application that carries any of them, narrowed
  by `team`, `project` or `environment` when those are given too. `POST /api/tags/{tag}/deploy`
  is now this same call with one tag, so it also skips what the caller may not deploy where it
  lives (it queued it before).

### Deprecated

Nothing.

### Removed

Nothing.

### Breaking changes

None. The baseline for version 1 is the API as 3.3.1 shipped it.

## 3.3.1 and earlier

The API had no version and no stated promise; `/api` was the only address. Version 1 starts from
what 3.3.1 served.
