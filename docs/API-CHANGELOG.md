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

### Deprecated

Nothing.

### Removed

Nothing.

### Breaking changes

None. The baseline for version 1 is the API as 3.3.1 shipped it.

## 3.3.1 and earlier

The API had no version and no stated promise; `/api` was the only address. Version 1 starts from
what 3.3.1 served.
