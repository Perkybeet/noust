# API

Everything the console does, it does through the API described here: the console is a
client of it like any script. The contract is exported as OpenAPI and served, authenticated,
at `GET /api/openapi.json`.

Examples use `https://panel.example.com` for the console's address and `$TOKEN` for a
credential. On a console bound to loopback, run them on the server against
`http://127.0.0.1:8080`, or through an SSH tunnel (see [console.md](console.md)).

## Versions and compatibility

The API has one version, **1**, and two addresses for it. `/api/v1/apps` and `/api/apps` are the
same route: not a copy, one route that answers to both names, so they have the same permission,
the same audit entry and the same rate limit. A script that wants a promise should use
`/api/v1`; the console and every client written before 3.4 use `/api` and keep working. If a
version 2 ever exists it appears at `/api/v2` beside this one, and `/api` stays version 1 until
version 1 is retired under the policy below. The event stream (`/events`), the WebSockets
(`/ws`) and the webhooks (`/hooks`) have no versioned address.

- **`X-Noust-API-Version: 1`** is on every response under `/api`, refusals included (a wrong
  credential, a blocked address, a rate limit), so a client can tell which API answered
  without reading a body.
- **The schema says which API it describes.** `GET /api/openapi.json` has `info.version: "1"`,
  the running release in `info.x-noust-version` and the versioned address in
  `info.x-noust-api-prefix`. The document lists each path once, as `/api/...`; the versioned
  address is the same path with `/api/v1` in place of `/api`.
- **A central talks to its nodes at `/api`**, which every node answers whatever its release, and
  forwards `/api/v1/...` calls to a node as `/api/...` for the same reason.

### The promise

Within a version, a client written against what a release shipped keeps working. That means a
release may **add**: a new operation, a new optional parameter or request field, a new field in a
response, a new response code. Clients must ignore response fields they do not know. A release
does not, without a deprecation period:

- remove an operation or answer it to another method;
- remove a documented response code, a response field, a parameter or a request field;
- add a required parameter or request field, make an optional one required, or ask for a body
  where there was none.

Everything else - wording of `detail` and `hint`, the order of keys or of list items, the
extensions - is not part of the promise; match on `error` codes, not on sentences.
`panel/openapi.baseline.json` is the API as a release shipped it, and a test
(`tests/test_api_compat.py`, also `python scripts/api_compat.py`) fails when the API stops keeping
it. The baseline is refreshed after each release, so what a release adds is promised from then
on.

### Deprecation

An operation that is going away is announced first, in the code, with
`noust.web.versioning.deprecated(since=..., sunset=..., successor=...)`:

- the schema marks it `deprecated: true` and carries `x-noust-deprecation`
  (`{since, sunset, successor}`);
- **every response of the route** carries `Deprecation: @<seconds>` (RFC 9745: when it was
  announced), `Sunset: <HTTP date>` (RFC 8594: the earliest day it may stop answering) and
  `Link: </api/v1/...>; rel="successor-version"`, so a client that never reads the schema still
  hears of it;
- the notice is **at least 180 days**, and the helper refuses a shorter one when the route is
  declared;
- it is listed in [API-CHANGELOG.md](API-CHANGELOG.md) in the release that announces it, and
  again in the one that removes it, which is never before its `Sunset`.

A client should log, or alert on, a `Deprecation` header in its own traffic. The exception is a
security fix: an operation that cannot be kept safe is changed at once, in a patch release, and
the change is in the changelog and in the security advisory (see [SECURITY.md](../SECURITY.md)).

An intentional break is accepted in the open: `python scripts/api_compat.py --update
--accept-breaking` rewrites the baseline in the same change as the code, and the entry goes to
the changelog under "Breaking changes".

## Authentication

Two ways in, for two kinds of client: tokens for scripts, sessions for browsers. Whoever comes
in, every route checks one permission (see [Permissions](#permissions)).

### Bearer tokens, for scripts

```bash
noust token create ci-update --scope deploy --owner alice    # prints the token once
noust token create dashboard --scope read --expires-hours 720
curl -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps
```

API tokens start with `noust_tok_` (the ones issued before 3.0 start with the old
product's prefix and keep working until revoked; `noust health` names the ones still in use),
are shown once, and are stored only as a salted hash. Manage them with
`noust token list|revoke`, the console's Settings > API tokens, or `GET|POST /api/auth/tokens`
and `DELETE /api/auth/tokens/{id}`. An account issues and lists its own tokens there; issuing
one from a browser session needs sudo mode.

`POST /api/auth/tokens` takes `name`, `scope` (`read`, `deploy` or `admin`), `expires_hours`,
`permissions` (a list to narrow it to, each held by the issuing account), `allowed_cidrs` (the
networks it is accepted from) and `allow_elevated` (whether it may call routes that need sudo
mode; off by default, refused under the ENS profile). A token acts for its owner and never holds
more than the owner's permissions, whatever its scope; if the owner's role changes or the owner
is disabled, the token follows at its next request. Under the `ens-medium` profile every token
expires within `auth.tokens.max_days` (90).

`team`, `project` and `environment` (slugs) bind the token to one of them: it sees that and nothing
else of the server, even when its owner is an administrator, and never reaches the machine. A bound
token must belong to an account. `GET /api/auth/tokens` says where each one reaches (`bound`) and when it
expires (`expires_at`). Every counted answer carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
`X-RateLimit-Reset` (the UNIX time at which the budget is whole again), so a pipeline can wait exactly as
long as it has to.

Tokens issued before the server had accounts keep their 3.0 scope and are never asked for sudo
mode. The first `admin` account adopts them. The master access token is also accepted as a
bearer credential: the whole console while no account exists, break-glass afterwards (see
[security.md](security.md#authentication)).

A token carries no session, so it needs no CSRF token. A *session* token presented as a Bearer
credential (see `bearer` below) is still a session: it needs the CSRF header and sudo mode
exactly like the cookie.

### Sessions, for browsers

```bash
curl -c jar -H 'Content-Type: application/json' \
  -d '{"username": "alice", "password": "...", "totp_code": "123456"}' \
  https://panel.example.com/api/auth/login
```

`POST /api/auth/login` takes `username` (or the email of the person, when it names one account),
`password` and `totp_code` (a TOTP code or a backup code) for an account, or `token` (the master
access token, plus `totp_code` when its 2FA is on). Without `totp_code`, an account with a second
factor answers `success: false` and `second_factor: {challenge, expires_in, methods}`: finish with
`POST /api/auth/login/second-factor` and `{challenge, code}`, or with a passkey by passing
`challenge` to `POST /api/auth/passkeys/login/options` and `/login`. That is how the console signs
in; the step is single use and lasts five minutes.
`bearer` also returns the session token in the body, for a client without a cookie jar; it then
sends `X-Noust-CSRF` like a browser does. Passkeys sign in with
`POST /api/auth/passkeys/login/options` and then `POST /api/auth/passkeys/login`. The answer is
`{success, expires_in, csrf_token, session_token}`, and two cookies are set:

- `noust_session`: `HttpOnly`, `SameSite=Strict`, `Secure` over TLS.
- `noust_csrf`: readable by the page, holding the CSRF token.

Every `POST`, `PUT`, `PATCH` and `DELETE` made with a session, in the cookie or as a Bearer
token, must echo the CSRF token in the `X-Noust-CSRF` header. `GET /api/auth/session` (no
credential required) reports whether the caller is signed in, its account and role, its
permissions, `expires_at`, `elevated_until`, whether a second factor is set up, and the CSRF
header and cookie names. It also says what confirming sudo mode takes: `elevation_factors`
(`totp`, `passkey`, `backup_code` for an account, those it has; `master_token` for the master
token without a second factor), `elevation_requires_password`, and the window itself,
`elevation_idle_minutes` and `elevation_max_minutes`. `elevated_until` moves later while
destructive actions are performed, up to that ceiling. Before sign-in it does not reveal the host
name, the version or whether 2FA is on. A credential presented to it that is wrong counts toward the lockout like
anywhere else; one the console signed that has merely expired does not.

A session ends after 30 minutes without activity and 12 hours in total (`auth.session.*`). An
account that has not enrolled a second factor gets `403 mfa_required` on everything but enrolling
one; one that has not accepted the usage notice (`auth.notice.text`) gets `403
notice_required` until `POST /api/auth/notice/accept`.

A failed sign-in answers `401` with the same `error` whatever was wrong (`invalid_credentials`
for an account; `invalid_token`, `totp_required` or `invalid_totp` for the master token). A TOTP
code is accepted once per purpose (signing in, `elevate`, disabling 2FA). Five failures from one
address lock it out for 15 minutes (`429`, `"error": "locked_out"`, with `Retry-After`), on every
request that carries a credential; five consecutive failures for one account lock that account
for 15 minutes.

Accounts are managed under `/api/auth/accounts`, invitations under `/api/auth/invitations`
(accepted at `/api/auth/invitations/open` and `/accept`, without a credential), passkeys under
`/api/auth/passkeys`, and an account changes its own password with `POST /api/auth/password`.

An account has an `email` (`PATCH /api/auth/accounts/{username}`), where mail goes and nothing else. An
invitation with `email` is also mailed (`emailed`, and `email_error` when the mail failed and the code is
to be handed over), and refused before anything is made when the server cannot send mail. A person who
forgot the password asks for a link at `POST /api/auth/password-reset/request` (`identifier`: the name or
the address; always `202`, the same whoever asks) and finishes at `POST /api/auth/password-reset/confirm`
(`code`, `password`, `second_factor`), both without a credential. They answer `404 reset_unavailable`
when the server has no mail channel or runs the ENS profile; `GET /api/auth/oidc/providers` says
whether the sign-in page offers it (`password_reset`).

Since 3.4 a person can also sign in through an identity provider (OpenID Connect), under
`/api/auth/oidc` ([sso.md](sso.md)). `GET /api/auth/oidc/providers` lists the buttons the sign-in page
shows (anonymous); `GET /start` and `GET /callback` are the browser's two redirects; `POST /complete`
collects the single-use ticket the callback left and answers what `POST /api/auth/login` answers
(`login`), so an account with a second factor is given the same `second_factor` step to finish. The
provider is a first factor only. `/config`, `/config/{provider}` (PUT in sudo mode), `/config/{provider}/test`
and `/identities` are the security officer's (`accounts.read`, `accounts.manage`).

SAML 2.0 providers sign in the same way under `/api/auth/saml` (`GET /start`, the provider's form posted to
`POST /acs`, `GET /metadata/{provider}` for the provider to register from, and the security officer's
`/config`), a directory takes a name and password at `POST /api/auth/ldap/login`, and a provisioning source
calls `/api/scim/v2` with the bearer token issued for it ([sso.md](sso.md)).

### Permissions

Every route needs one permission, published in `/api/openapi.json` as the operation's
`x-noust-permission`: `public` (no credential), `self` (any signed-in principal, acting on its
own session, tokens, second factor and passkeys), or one of `apps.read`, `apps.operate`,
`apps.deploy`, `apps.manage`, `secrets.reveal`, `root_equivalent`, `server.read`,
`server.manage`, `server.host_access`, `databases.read`, `databases.write`, `databases.manage`,
`backups.read`, `backups.run`, `backups.manage`, `fleet.read`, `fleet.manage`, `settings.read`,
`settings.manage`, `security.manage`, `accounts.read`, `accounts.manage`, `audit.read`,
`audit.manage`, `compliance.read`, `teams.read`, `teams.manage`. `GET /api/auth/roles` lists
what each role holds; the table is in [security.md](security.md#roles-and-permissions).

Since 3.4 an account also belongs to **teams** ([teams.md](teams.md)), and the permission of a route
that names a resource is held *in the resource's team*: a principal that does not see the resource
gets `404` (the same body as for one that does not exist, before anything else is asked of it),
and one that sees it but holds the permission only in another team gets `403`. A route about the
whole server answers `403` with `"error": "instance_scope_required"` to an account that does not see
every team. `GET /api/teams/mine` says which teams the caller sees and which one it works in.

A role can be held at a scope narrower than the server (an environment, a project, a team), and
the permission of a route about a resource is then the union of what the caller holds on the
environment, the project and the team that contain it. `GET|POST /api/access/bindings` and
`DELETE /api/access/bindings/{id}` give and take a role at a scope, and `GET|POST /api/access/roles`,
`GET|PATCH|DELETE /api/access/roles/{role}` define roles of your own (see
[teams.md](teams.md#roles-where-they-are-needed)).

A credential without the permission gets `403` with `"error": "permission_denied"` and a
detail naming the permission. A 3.0 scope maps onto permissions: `read` is a viewer, `deploy` a
viewer that may also deploy (`apps.deploy`), `admin` everything.

A `source` that is a local path rather than a repository URL is accepted by `POST /api/apps`
and `POST /api/apps/inspect` only from the master token or a session in sudo mode (`403`
`elevation_required` until it confirms). An API token gets `403`, whatever it holds.
Application reads show a credential stored inside a clone URL as `***`.

### Sudo mode

Routes that need a session to confirm its operator are marked in the schema with
`x-noust-requires-elevation`: deleting things, restoring, revealing secrets, writing an `.env`,
raw units and site configuration, server changes, account management, issuing tokens, and
the rest listed in [security.md](security.md#sudo-mode). Without a recent confirmation they
answer:

```json
HTTP/1.1 403 Forbidden

{"error": "elevation_required", "detail": "Confirm it's you to continue",
 "hint": "POST /api/auth/elevate with your two-factor code, or the master token.",
 "fields": null, "output": null}
```

`POST /api/auth/elevate` with `{"password": "...", "code": "123456"}` for an account (TOTP or
backup code), `{"code": "123456"}` for the master token with 2FA, or `{"token": "noust_..."}`
when it has none, elevates the session for 10 minutes and answers `{"elevated_until": "..."}`.
A passkey does the same through `POST /api/auth/passkeys/elevate/options` and
`/api/auth/passkeys/elevate`. Then retry the request. Tokens issued before 3.1 and the master
token are not asked; a token issued since is refused on these routes unless it has
`allow_elevated`. A session is always asked, whichever header carries it.

### Approvals

When four-eyes approvals apply (`approval.enabled`, or the `ens-medium` profile;
`GET /api/approvals/policy` says), the routes marked `x-noust-requires-approval` in the schema
do not run on the first call. The schema publishes the mark whether approvals are on or not,
with the rule's `action`, `kind` and `when` (always, or the condition on the body, such as
`mode: "write"` for SQL). The first call answers:

```json
HTTP/1.1 202 Accepted
Location: /api/approvals/17
X-Noust-Approval-Request: 17

{"error": "approval_required",
 "detail": "This needs a second person's approval. Request 17 is waiting for a security account to decide it.",
 "hint": "Once approved, send exactly the same call again with the header X-Noust-Approval: 17, within 30 minutes.",
 "fields": {"approval": "17", "state": "requested"}}
```

Send `X-Noust-Reason` (percent-encoded UTF-8) with the first call to say why; the `ens-medium`
profile requires it. Another person decides with `POST /api/approvals/{id}/approve` or
`/reject` (sudo mode), or `noust approval approve ID` as root. Then the requester sends the same
call again, byte for byte, with `X-Noust-Approval: 17`; it runs once. `GET /api/approvals` lists
requests (`state=requested|approved|rejected|expired|executed|all`).

## Errors

Every error from every router has the same shape:

```json
{
  "error": "validation_error",
  "detail": "What happened",
  "hint": "How to fix it, or null",
  "fields": {"domain": "Not a valid domain"},
  "output": "The system's own output (nginx -t, systemctl, certbot), or null"
}
```

- `error` is a stable machine code. For Noust's own exceptions it is the exception class in
  lower case (`deploymenterror`, `certificateerror`, `serviceerror`, ...); otherwise one of
  `unauthorized`, `forbidden`, `permission_denied`, `not_found`, `conflict`,
  `validation_error`, `rate_limited`, `locked_out`, `elevation_required`, `approval_required`,
  `mfa_required`, `notice_required`, `payload_too_large`, `app_busy`, `invalid_credentials`,
  `invalid_token`, `totp_required`, `invalid_totp`, `internal`.
- `detail` is one sentence for a person. `hint` is the suggested fix, or `null`.
- `fields` is set on `422` validation errors, `null` otherwise: a map from field name to
  message, which the console shows next to each form control.
- `output` carries a system tool's own output verbatim, when there is one. Show it in a
  monospace block; do not paraphrase it.

Refusals made before routing (address not allowed, TLS required, body too large, rate limit,
lockout) have the same keys except `output`.

**`schema_changed`** (`409`, since 3.2) is the refusal of every way back: activating a release,
rolling back to a deployment, restoring a backup's files, and the job that restores a backup
for a rollback. Noust puts code back, never a database, so going back past one or more
deployments that changed the schema waits for the caller's yes. The body has the usual keys
plus `deployments`, the ids of those deployments, oldest first, and `fields` names
`schema_changed_ok`. Repeat the call with `schema_changed_ok` set to confirm (a query parameter
on `POST /api/apps/{domain}/releases/{release_id}/activate`, a body field everywhere else), or
restore the database from the backup taken before the first of them. See
[releases.md](releases.md#going-back-past-a-schema-change).

| Status | When |
|---|---|
| `400` | Invalid input Noust checked itself: a domain, a name, a path, a configuration value, a source; a `Host` not in `web.allowed_hosts` |
| `401` | No credential, or an invalid or expired one |
| `202` | `approval_required`: the call became a four-eyes request; see [Approvals](#approvals) |
| `403` | Permission missing, sudo mode required, second factor or notice pending, address not allowed |
| `404` | Unknown application, database, job, release... |
| `409` | Conflict: the domain is taken, the database exists, the application is in place and has no releases; `app_busy` when another deploy, update, rollback, migration, restore or deletion is running on the application (`detail` names it; wait for it, or follow it in Jobs); `schema_changed` when going back would pass deployments that changed the database schema (see below); `rollback_unavailable`, with the reason; `adopt` refused because starting the stack as Noust would recreate containers (`output` has Compose's own dry run) |
| `413` | Request body over the limit, checked before authentication: 1 MiB, or 5 MiB under `/hooks/` |
| `422` | Request body failed validation; see `fields` |
| `429` | Rate limit (120 requests a minute per address by default) or lockout |
| `500` | A system operation failed; `detail`, `hint` and `output` say which and why |

## Conventions

- **Timestamps** are ISO 8601 with an explicit offset.
- **Long operations run as jobs.** Creating an application, updating, deleting, backing up,
  restoring and issuing certificates answer `202` with `{job_id, status, message, job}`.
  Follow the job with `GET /api/jobs/{id}`, its log with `GET /api/jobs/{id}/log?tail=N`, the
  `job` events on `/events`, or `/ws/jobs/{id}`. A finished deploy or update job carries the
  `deployment_id` of its history row. Jobs and their logs are persisted. A job left
  running when the console restarts is marked failed with "Interrupted by a panel restart",
  except one whose work runs in a transient systemd unit of its own (an operating system
  update, Noust updating itself): it records that unit, and the console that starts again reads
  how the unit ended instead of guessing. A unit that ended well completes the job, with what
  the unit wrote appended to its log; one that failed fails it with systemd's result and the
  unit's own words; one still running keeps the job running until it ends, or until that kind
  of job's deadline, which the log says. This matters because the `noust` package among the
  upgrades is what restarts the console. A central's fleet job waits for the node to come back
  and reads the reconciled result rather than counting a failure. Each reconciliation is
  audited. A job with no recorded unit is still marked interrupted.
  A deploy or an update that is **building** can be cancelled too (`POST /api/jobs/{id}/cancel`):
  its build is stopped, the release it was making is removed, the deployment is recorded
  as cancelled (`cancelled: true` on `GET /api/deployments/{id}`; its status stays `failed`)
  and what served before is never touched. The answer is `{message, job_id, state}` with
  `state` `cancelled` for a job that waited and `cancelling` for one that was building; from
  the moment its release is being activated it is `409 cancel_refused`, as it is for a job
  that ended or that cannot be interrupted (a restore, an in-place build).
- **The deploy queue.** A push or a request that arrives while an application builds waits
  its turn (`409 app_busy` is for an operation of another kind): `GET /api/jobs/queue` lists
  what runs and what waits, with the limits, and every job carries `state` (`queued`,
  `running`, `succeeded`, `failed` or `cancelled`), `queue_position`, `waiting_for`,
  `cancellable` and, for a running job that cannot be cancelled, `cancel_blocked_reason`
  (`activating`, `builds_in_place` or `unsupported`). Builds of different applications overlap up to `deploy.max_concurrent_builds`;
  requests for the same update merge, and `POST /api/jobs/update` says so in its `message`.
  When `deploy.queue_limit` builds wait, the next is `429 queue_full` with `Retry-After`.
  A build that runs past `deploy.max_minutes` (60 by default; 0 is no limit) is stopped as a cancel would
  stop it and ends as a failure whose message says the time limit was the reason; its job and its
  deployment are `failed`, not `cancelled`.
- **Lists** answer an object with the items and a count: `{apps, total}`,
  `{backups, total}`, `{items, total}`, and so on. The one exception is
  `GET /api/system/disks`, a bare array.
- **Pagination.** Two endpoints page with a keyset cursor:
  - `GET /api/deployments?limit=50&before_id=N`: answers `{items, total, next_before_id}`;
    pass `next_before_id` back as `before_id` until it is `null`. `limit` is at most 200.
    Filters: `domain`, `status` (`queued`, `running`, `success`, `failed`, `rolled_back`),
    `trigger` (`panel`, `cli`, `webhook`).
  - `GET /api/audit?limit=50&before=T`: answers `{items, next_before}`. `limit` at most 200.
    Filters: `action`, `result`, `actor`, `category`, `correlation_id`, `target`. Needs
    `audit.read`.

  Other lists take a `limit` and return the newest entries: `GET /api/jobs` (at most 100),
  `GET /api/backups` (at most 1000), `GET /api/monitor/observations` and the process lists
  (at most 500). There is no `offset` or `page` parameter anywhere.
- **Logs** take a tail: `GET /api/apps/{domain}/logs?lines=N` (at most 1000),
  `GET /api/jobs/{id}/log?tail=N` (lines), `GET /api/deployments/{id}/log?tail=N` (bytes).

## Driving a server from elsewhere

The API is the whole product: the command line can drive another server through it, with a context
(a URL and where to read a token from) and no SSH. See [remote.md](remote.md). A manifest of a
server's applications is planned and applied through it (`POST /api/manifest/plan` previews one and
`POST /api/manifest/apply` applies it as a job, as the caller; [manifest.md](manifest.md)), `POST /api/jobs/update-many` updates every application of a team, a
project, an environment or a tag in one call, and `POST /api/mcp` speaks the Model Context Protocol for clients
that inspect, deploy and control with an API token ([mcp.md](mcp.md)).

The console lists every route with the permission it needs and can try any of them: Settings > API
reference sends the call with the session you are signed in with (a JSON body to edit, the status, the
headers and the server's own words back), so your role, sudo mode and a second person's approval apply
as on any page, and a change asks before it goes. It loads nothing from outside: no Swagger UI, no CDN.
The same document is `GET /api/openapi.json`.
A typed Python client generated from it is `sdk/python` ([sdk.md](sdk.md)), and a Terraform and OpenTofu
provider written against it is `terraform-provider-noust/` ([terraform.md](terraform.md)).

## Realtime

### `GET /events` (Server-Sent Events)

One stream carries everything the console updates live. It authenticates like any `GET`:
the session cookie or any bearer token.

```bash
curl -N -H "Authorization: Bearer $TOKEN" https://panel.example.com/events
```

The stream starts with a `: connected` comment and sends a `: keepalive` comment after 25
seconds of silence. It sends no `id:` or `retry:` fields: after a disconnection, reconnect and
refetch what you display. A client that cannot keep up loses the oldest queued frames. The
credential is checked again every 25 seconds; once it is revoked, rotated or expired the
stream ends, and the reconnection answers `401`.

| Event | When | Data |
|---|---|---|
| `machine` | Every 5 seconds | `{hostname, uptime_s, load: [1m, 5m, 15m], load_history, cpu_percent, memory: {used, total, percent}, disk: {used, total, percent}, units: {running, failed, stopped}, apps: {running, failed, stopped, static}}`. Same object as `GET /api/system/machine`. |
| `metrics` | Every 2 seconds | A flat map of the latest samples: `cpu.percent`, `mem.used_bytes`, `mem.total_bytes`, `swap.used_bytes`, `disk.used_bytes`, `disk.total_bytes`, `net.rx_bytes_s`, `net.tx_bytes_s`, `load.1m`, and per application `app.<domain>.cpu.percent`, `app.<domain>.mem.bytes`. |
| `job` | On every job transition and log line | The job as `GET /api/jobs/{id}` returns it, with `logs` trimmed to the newest entry, plus `domain`, `message`, `level` and `finished`. |
| `state` | With every `job` event | `{id, state}` for the job, and again for its domain when it has one. `state` is `busy`, `active`, `failed` or `idle`. |
| `notice` | When a job completes, fails or is cancelled | `{text, state}`: a one-line message for a toast. |
| `app` | When an application changes (a job on it ends, an action on it succeeds) | The application as `GET /api/apps/{domain}` returns it; `{domain, status: "deploying"}` while a deploy, update or restore runs; `{domain, deleted: true}` when it is gone. |

### WebSockets

| Path | Streams |
|---|---|
| `/ws/logs/{domain}?lines=N` | The application's journal: the last `N` lines (1 to 500, default 50), then follows. `{domain}` may also name a unit Noust manages; any other unit is refused. |
| `/ws/jobs/{id}` | One job, until it finishes. |
| `/ws/jobs` | Every job's transitions. |
| `/ws/events` | Journal entries of Noust's own units: `noust-*` (cron and backup timers, the monitor), and the units of applications from before 0.14.1, which still carry the legacy prefix. |

A handshake authenticates with any one of:

- the session cookie (a browser on the console's own origin);
- `Authorization: Bearer <token>` (any client that can set headers);
- the subprotocol header `Sec-WebSocket-Protocol: noust.auth, noust.token.<token>` (the
  spelling of before 4.0 is still read, and answered in kind);
- `?ticket=<ticket>`, from `POST /api/auth/ws-ticket` (answers `{ticket, expires_in}`): single
  use, valid for 30 seconds, and bound to the address it was issued to. This is how the
  console connects. Tickets are issued for cookie sessions; a script should use the header.

A long-lived token is never accepted in the query string. A handshake from a foreign
`Origin` is refused.

Close codes: `4401` not authenticated, or the credential stopped being valid while the socket
was open; `4403` forbidden (origin, address, or a unit Noust does not manage); `4408` the
socket reached its 12 hour lifetime, reconnect; `4429` rate limited, locked out, or the
credential already holds 8 open sockets.

An open socket re-checks its credential every 30 seconds: revoking the API token, rotating
the master token or signing out closes it with `4401` (after an `{"type": "error"}` frame).
A session renewal does not close it.

Frames are JSON:

- `/ws/logs/{domain}` sends `{"type": "connected", "domain", "service"}`, then
  `{"type": "log", "data": "<line>"}` per journal line, `{"type": "warning", "data"}` for
  journalctl's own complaints and `{"type": "error", "message"}`. Every socket lasts at most
  12 hours; reconnect after that.
- `/ws/jobs/{id}` sends `{"type": "connected", "job"}`, `{"type": "update", "job"}` on every
  change, `{"type": "finished", "job"}` once the job completes, fails or is cancelled, and
  `{"type": "heartbeat"}` after 30 quiet seconds. An unknown id gets `{"type": "error"}` and
  the socket closes.
- Every socket answers `{"type": "ping"}` with `{"type": "pong"}`. `/ws/jobs/{id}` also
  accepts `{"type": "cancel"}` from a credential holding `apps.operate`; anyone else is
  answered with an error and nothing is cancelled.

```bash
websocat -H "Authorization: Bearer $TOKEN" \
  "wss://panel.example.com/ws/logs/shop.example.com?lines=100"
```

## Examples

Create an application and follow its deploy (a credential holding `apps.manage`):

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"domain": "shop.example.com", "source": "https://github.com/you/shop.git"}' \
  https://panel.example.com/api/apps
# 202 {"job_id": "a1b2c3d4", "status": "pending", "message": "...", "job": {...}}

curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/jobs/a1b2c3d4
curl -s -H "Authorization: Bearer $TOKEN" "https://panel.example.com/api/jobs/a1b2c3d4/log?tail=200"
```

`POST /api/apps` accepts `domain`, `source`, `app_type` (default `auto`), `port`,
`webserver`, `branch`, `ssl`, `env_vars`, `layout` (`inplace` or `releases`; omitted, the
server's `deploy.layout`), `include_www`, `persistent_paths`, `memory_max_mb`,
`cpu_quota_percent`, `tasks_max`, and for monorepos and Compose projects
`subdomain_overrides`, `workspace_filter`, `skip_database`, `compose_file`,
`compose_profiles`. `POST /api/apps/inspect` with `{"source", "branch"}` previews what would be
deployed (type, commands, port, variables from `.env.example`) without deploying anything.

Update, and roll back to a release:

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"domain": "shop.example.com"}' https://panel.example.com/api/jobs/update

curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/releases
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.example.com/api/apps/shop.example.com/releases/20260924-101500-9f8e7d6/activate
# {"domain", "release_id", "previous_id", "changed", "rolled_back", "deployment_id"}
```

Activation answers when the release has passed the health gate, or fails with the probe's and
the journal's output after putting the previous release back.

Why is it down:

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/diagnose
# {"domain", "verdict": "down", "probable_cause": "...", "checks": [{"name", "status", "summary", "evidence"}]}
```

Deployment history:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://panel.example.com/api/deployments?domain=shop.example.com&status=failed&limit=20"
curl -s -H "Authorization: Bearer $TOKEN" "https://panel.example.com/api/deployments/42/log"
```

A deployment says what its hooks did and whether it changed the database's schema (3.2):
`schema_changed`, `schema_changed_between` (the later deployments of the application that did:
going back to this one passes them), `hooks` (each hook, and Prisma's automatic migration with
`automatic: "prisma"`, with `phase`, `run`, `service`, `migrates`, `exit_code`, `ok`,
`timed_out`, `duration_s` and the end of its `output`) and `warnings` (why a successful
deployment went live with warnings: a `post_deploy` hook failed). `GET /api/apps/{domain}`
carries `follow_tags` (the glob of tags it deploys, or null) and `backup_before_update`.

### Deploy hooks and going back past a migration

```bash
# Which hooks the next deployment runs, and where they come from
curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/hooks
# {"domain", "source": "repo" | "operator" | "none", "pre_deploy": [...], "post_deploy": [...],
#  "document": null, "repository_error": null}

# Set the operator's own, which replace the repository's noust.yaml whole (sudo mode, four-eyes)
curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"document": "hooks:\n  pre_deploy:\n    - run: ./scripts/migrate.sh\n      timeout: 300\n      migrates: true\n"}' \
  https://panel.example.com/api/apps/shop.example.com/hooks
# 200 the same shape, "source": "operator". An invalid document answers 400 and names the field:
# {"error": "validationerror", "fields": {"hooks.pre_deploy[0].timeout": "..."}}

curl -s -X DELETE -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/hooks
```

`document` is the shape of a `noust.yaml` holding only `hooks` (at most 64 KiB); `source:
"operator"` means it replaces the repository's. Writing hooks is root-equivalent because a hook
runs with the application's identity and secrets. `repository_error` says why the running code's
own `noust.yaml` is not valid, when it is not: the next deployment fails with it. See
[compose.md](compose.md#noustyaml) and [compose.md](compose.md#operator-hooks).

### Container applications

A container application (a Dockerfile, an image from a registry, or Railpack; see
[containers.md](containers.md)) is created through the same `POST /api/apps`, with its settings
in `container`. An image needs no `source`:

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"domain": "shop.example.com", "container": {"image": "ghcr.io/owner/app:1.4",
       "container_port": 8080, "registry_username": "ci", "registry_password": "..."}}' \
  https://panel.example.com/api/apps
# 202 {"job_id": "...", ...}   the registry password is in the secret store by now

curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/container
# {"domain", "method", "image", "dockerfile", "context", "target", "build_args", "build_secrets",
#  "container_port", "volumes", "restart_policy", "registry_server", "registry_username",
#  "registry_password_set", "digest", "release"}   never the password, nor a secret's value

curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"target": "runtime", "build_secrets": {"npmrc": "<value>"}, "clear": ["container_port"]}' \
  https://panel.example.com/api/apps/shop.example.com/container
```

`PUT` changes what it names (`clear` resets `dockerfile`, `target`, `container_port` or
`registry_server`; a build secret set to `null` is removed; `clear_registry` removes the account)
and the change takes effect with the next update. The releases of a container application are its
images: `GET .../releases` lists them (`on_disk` says whether the image is still on the server)
and `POST .../releases/{id}/activate` recreates the container from one, with no build.
`GET .../logs` answers with what the container printed, secrets masked.
### Build block

```bash
# Which commands, base directory, publish directory and runtime the next deployment uses
curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/build
# {"domain", "source": "repo" | "operator" | "none", "base_directory", "install", "build", "start",
#  "publish_directory", "runtime", "document": null, "repository_error": null,
#  "applied": {"start", "working_directory", "path", "runtime"} | null}

# Set the operator's own, which replaces the repository's noust.yaml whole (sudo mode)
curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"base_directory": "apps/web", "build": "pnpm --filter web build", "runtime": "node@22"}' \
  https://panel.example.com/api/apps/shop.example.com/build
# 200 the same shape, "source": "operator". An invalid block answers 400 and names the field:
# {"error": "validationerror", "fields": {"build.base_directory": "..."}}

curl -s -X DELETE -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/build
```

The keys are `base_directory`, `install`, `build`, `start`, `publish_directory` and `runtime`; a
key left out is not set, and an empty body is refused (delete the block to give the repository
its say back). Writing it is root-equivalent: its commands run in the build sandbox and, for
`start`, as the application with its secrets. It applies from the next deployment or update,
which also writes the unit again when the command, the directory or the runtime changed. See
[build.md](build.md).

Going back to a deployment that would pass one that changed the schema is refused until it is
confirmed:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.example.com/api/apps/shop.example.com/deployments/41/rollback
# 409 {"error": "schema_changed", "detail": "Going back to deployment 41 of shop.example.com
#      passes deployment 43, which changed the database schema", "hint": "...",
#      "fields": {"schema_changed_ok": "..."}, "deployments": [43]}

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"schema_changed_ok": true}' \
  https://panel.example.com/api/apps/shop.example.com/deployments/41/rollback
# 202 {"job_id": "...", ...}
```

The same field, `schema_changed_ok`, confirms `POST /api/jobs/rollback` and
`POST /api/backups/{backup_id}/restore` (in the body), and
`POST /api/apps/{domain}/releases/{release_id}/activate` (as a query parameter).

### Adopting a Docker Compose stack

`POST /api/apps/adopt` registers a stack that already runs, without touching it. It needs
`apps.manage` and sudo mode. `preview: true` answers what the adoption would record and changes
nothing, which is what the console shows before asking:

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"domain": "proggest.es", "path": "/opt/proggest", "preview": true}' \
  https://panel.example.com/api/apps/adopt
# {"domain", "app_name", "app_path", "compose_file", "project", "project_from", "containers",
#  "running", "source", "branch", "commit", "site", "site_name", "ssl", "port", "headless",
#  "dry_run", "changes", "warnings", "adopted": false, "unit", "deployment_id": null}
```

The request also takes `compose_file`, `source`, `branch`, `site` (the file in `sites-available`
that serves the domain, when its name is not the domain's), `port` and `accept_recreate`. When
`docker compose up --dry-run` says starting the stack as Noust would create, recreate or remove
something, the call answers `409` with Compose's own output in `output` and the lines in
`changes`, and adopts nothing unless `accept_recreate` is true. `dry_run` carries that output
verbatim on success. See [compose.md](compose.md#adopting-a-running-stack).

The same family: `PATCH /api/apps/{domain}/backup-before-update` with `{"enabled": false}`
(sudo mode; answers `{domain, backup_before_update, previous}`) switches off the copy of a
stack's databases an update takes first; `GET /api/apps/{domain}/headless` says whether a
Compose stack publishes no port (`headless`), which port is still recorded (`recorded_port`) and
whether its site can go too (`site_retirable`), and `POST` with `{"remove_site": true}` records
it as a worker (sudo mode); `PATCH /api/apps/{domain}/follow-tags` with `{"pattern": "v*"}` (or
`null`) makes it deploy tags instead of a branch. `PUT /api/apps/{domain}/zero-downtime` also
accepts a Compose stack, for which the mode means each web service is updated behind a relay:
`instances` is empty and `reason` and `hint` say what is missing (a published port, the line an
operator's own site must include). See
[compose.md](compose.md#zero-downtime-updates-the-relay),
[compose.md](compose.md#stack-database-backups-and-restore),
[compose.md](compose.md#workers-without-a-web-port) and
[compose.md](compose.md#deploying-by-tag).

`DELETE /api/apps/{domain}` (and `POST /api/jobs/delete`) never removes an adopted stack's
directory, which is outside Noust's apps directory, with the files; name it exactly in
`remove_adopted_directory` to remove it too. The operator's own site and the servers files it
includes are kept and reported as kept.

### Reading a site: structure, edits, routes

None of these writes anything; the console's visual editor turns their results into text and
saves it through `PUT /api/sites/{domain}/config`, the one way a site is written. All need
`apps.read`.

```bash
# The model of the saved file: servers, locations in the order the web server tries them,
# upstreams, includes (resolved when they are Noust's own or under /etc/nginx), comments, and
# every directive it has no field for, as raw text
curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/sites/proggest.es/structure
# {"site", "webserver", "path", "structure": {"kind": "nginx", "servers": [...], "upstreams": [...],
#  "includes": [...], "directives": [...], "notes": [...]}, "error": null}

# The same for a draft; text that does not parse is an answer, not a failure
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"config": "server { listen 80;"}' https://panel.example.com/api/sites/proggest.es/structure
# {"site", "webserver", "path", "structure": null, "error": {"line", "column", "message"}}

# Apply edit operations to a text, in order; the result is text, and only the bytes of the
# element each operation names change
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"config": "...", "ops": [{"op": "set_directive", "parent": "s0/l3", "name": "proxy_read_timeout", "args": ["120s"]}]}' \
  https://panel.example.com/api/sites/proggest.es/config/edit
# {"config": "...", "structure": {...}, "changed_lines": 1}

# Which server and location answer a request, and why (a draft in "config", or the saved file)
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"host": "proggest.es", "path": "/api/v1/auth/login", "scheme": "https"}' \
  https://panel.example.com/api/sites/proggest.es/route
# {"server_id", "location_id", "steps": ["one sentence per step"], "trace": [{"code", "params", "text"}],
#  "highlight": [ids along the path, the upstream last as "u:<name>"], "redirect": null}
```

Ids are positions: `s1` is the second server, `s1/l3` its fourth location, `u:name` an
upstream, `d3` a directive. The operations are `set_directive`, `add_directive`,
`remove_directive`, `add_block` (with a `body` or a `template` of `proxy`, `static` or
`redirect`), `remove_block`, `duplicate_block` and `move_block`; at most 200 per request and a
text of at most 1 MiB. A malformed operation answers `400` naming it (`ops[2].target`); text that
does not parse answers `400` with the line.

`GET /api/sites/{domain}/topology` is the structure plus what is behind it now: for each
address the site reaches, who holds the port (a Noust application, a Compose service by its
labels, a container, a systemd unit, a process), whether it accepts a connection (only on this
machine, a one second connection, cached for ten seconds) and, for each server, the expiry of
its certificate. `POST /api/sites/{domain}/config/test` tests the candidate inside the live
`nginx.conf` (or `apache2.conf`), with this site's file swapped for it, so a site that uses a
`limit_req_zone`, a `map` or a `log_format` declared there passes and a second `upstream` of an
existing name fails, as at the reload. `GET /api/sites` lists the files in the web server's
sites directory together with the store's: each entry says whether Noust wrote it
(`noust_managed`), the application it serves (`app`) and the file's name (`site_name`), which is
not the domain for a site the operator named. A site the operator wrote is never rewritten by a
deploy; see [domains.md](domains.md#operator-sites).

### What happened in a stretch

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://panel.example.com/api/timeline?start=1790000000&end=1790003600&app=shop.example.com&sources=journal,audit"
# {"start", "end", "app", "events": [{"at", "source", "kind", "level", "text", "unit", "app",
#  "actor", "status", "ref", "priority", "details"}], "processes": {"minutes": [{"at", "cpu": [...],
#  "memory": [...]}], "total_minutes", "since", "commands"}, "sources": [{"source", "state", "count",
#  "truncated", "permission", "message", "evidence"}]}
```

`start` and `end` are epoch seconds, at most 31 days apart and not in the future. `app` narrows
the answer to one application; `sources` (repeated or comma-separated) to some of `journal`,
`audit`, `deployments`, `jobs`, `monitor` and `processes`. The route needs `server.read`, and
each source keeps its own permission (`journal` needs `secrets.reveal`, `audit` needs
`audit.read`); a source the caller may not read is answered with `state: "withheld"` and the
permission it lacks, never dropped, and one that failed says why in `message` and `evidence`.
`processes.commands` says whether the redacted command lines are included (only for callers
holding `secrets.reveal`). See [MONITOR.md](MONITOR.md#what-happened-in-a-stretch).

`GET /api/server/clock/timezones` lists the zones the managed server knows, each with its
region, city, offset now (`UTC+02:00`, with `offset_minutes`) and abbreviation, `Etc/UTC` and
`UTC` first and the rest by offset and name; the server computes it, so on a central the node
answers.

## Deploy webhooks

`POST /api/apps/{domain}/webhook-secret` (`apps.manage`, sudo mode) creates or replaces an application's webhook
secret and answers `{domain, secret, hook_url}`; the secret is shown once. Configure the forge
to send push events to `hook_url`, which is `https://<console>/hooks/deploy/{domain}`:

| Forge | Header checked |
|---|---|
| GitHub | `X-Hub-Signature-256: sha256=<HMAC-SHA256 of the body>` (set the webhook secret) |
| Gitea | `X-Gitea-Signature: <HMAC-SHA256 of the body>` |
| Forgejo (Codeberg) | `X-Forgejo-Signature: <HMAC-SHA256 of the body>`; it sends Gitea's headers too, and the delivery log says `forgejo` |
| Gogs | `X-Gogs-Signature: <HMAC-SHA256 of the body>`, no prefix; the event is in `X-Gogs-Event` (and `X-GitHub-Event`) |
| GitLab | `X-Gitlab-Token: <secret>` |
| Bitbucket Cloud | `X-Hub-Signature: sha256=<HMAC-SHA256 of the body>` (set the webhook's secret); the event is in `X-Event-Key` |

Bitbucket Cloud (3.4): add the webhook at the repository's Repository settings > Webhooks with
the triggers `Repository: Push` and, for previews, `Pull request: Created, Updated, Merged,
Declined`. A push (`repo:push`) deploys the tracked branch (a push that moves several branches
deploys when one of them is it), `diagnostics:ping` is answered, and the four pull request keys
build, rebuild (only when the commit at the tip of the branch is new: Bitbucket sends
`pullrequest:updated` for a new title too) and remove previews; merged and declined both end
the preview. A request from a fork is refused, a bot's unless previews allow bots, and no
comment is posted on it. Private repositories are cloned with a
[deploy key](#deploy-keys) or an [https credential](#deploy-keys) kept as a secret: app
passwords stopped working on 28 July 2026.

A push to a branch other than the one the application deploys (its pinned branch; without a
pin, the branch its checkout is on in place or its recorded branch on releases) answers
`200 {"status": "ignored", "reason": "branch"}`; with no branch at all to go by, any push
deploys. A forge's ping is answered and recorded; a forge retry of the same delivery answers `"reason": "duplicate"`.
An application that **follows tags** (`noust app follow-tags DOMAIN 'v*'`, `PATCH
/api/apps/{domain}/follow-tags`) reads its deliveries differently (3.2): a published `release` of
GitHub, Gitea or Forgejo (not a draft or a pre-release) and a pushed tag (GitHub, Gitea, Forgejo, Gogs, GitLab or Bitbucket)
deploy that tag, once even though a forge may send both for one version (`202 {job_id,
status}`); a draft or a pre-release, a tag that does not match the pattern, is not a version,
or is not newer than what is deployed answers `200 {"status":
"ignored", "reason": "tag", "detail": ...}` and a release action that publishes nothing
answers `200` with `"reason": "action"`; and a push to a branch is ignored (`"reason":
"branch"`) because there is no branch to deploy. The update job re-checks the tag when it runs.
See [compose.md](compose.md#deploying-by-tag).
An accepted delivery queues an update (`202 {job_id, status}`), exactly like `noust update`.
An unknown application and one without a secret both answer `404`; a bad signature answers
`401` and counts against that application: after 10 in 15 minutes its hook answers `429`
(`"error": "locked_out"`, `Retry-After`) for 15 minutes. Other applications, and the forge's
address, are not affected. A delivery larger than 5 MiB answers `413`. `DELETE /api/apps/{domain}/webhook-secret` turns
webhooks off. `GET /api/apps/{domain}/webhook` reports how far the setup is (public URL,
secret, branch, GitHub App), `GET /api/apps/{domain}/webhook/received` lists every delivery
whatever became of it (a burst of wrong signatures is one row with a count), and
`GET /api/apps/{domain}/webhook/deliveries` lists the deployments webhooks triggered. The same
from the terminal: `noust app webhook show|rotate|disable|deliveries DOMAIN`. The hook must be reachable from the forge, which means exposing the console (see
[security.md](security.md)).

## Deploy keys

Each repository an application is cloned from can have an SSH key of its own (3.4), instead of the
one key `noust setup ssh` manages for the whole server: a key whose theft reaches one repository,
and which GitHub accepts, since it refuses one deploy key on two repositories.

```bash
# See what the repository is reached with (the key's public half, never its private one)
curl -s -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps/shop.example.com/deploy-key
# Make an ed25519 key with no passphrase (or {"key": "name"} to assign one that exists)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}' \
  https://panel.example.com/api/apps/shop.example.com/deploy-key
# Replace it without a moment when the repository has none that works
curl -s -X POST -H "Authorization: Bearer $TOKEN" .../deploy-key/rotate   # add key.pending.public_key at the host
curl -s -X POST -H "Authorization: Bearer $TOKEN" .../deploy-key/test     # the host answers: the new key is the key
```

`POST .../deploy-key/import` (sudo mode) takes `{private_key, name}`; the public half is derived
with `ssh-keygen -y` and a key with a passphrase is refused. `DELETE .../deploy-key` (sudo mode)
takes the repository off its key and deletes the key when nothing else uses it. The private
half is a 0600 secret file, never in an argument or a response; git is given its path
(`ssh -i <key> -o IdentitiesOnly=yes`). Host keys are pinned in a `known_hosts` of Noust's own
the first time each code host is reached, and a different key later is refused, never accepted
silently: `GET /api/deploy-keys/hosts` lists them and `DELETE /api/deploy-keys/hosts/{host}` (sudo
mode) forgets one, the one deliberate way to accept a change; `{host}` is a name, `name:port`
or the `[name]:port` the list gives a host on another port. The first connection does not have
to be trust on first use: `POST /api/deploy-keys/hosts` (sudo mode, `{host, fingerprint}`) asks the
host for its keys and records only the one whose `SHA256:` fingerprint the operator brings from
the code host's own published list (GitHub's and GitLab's documentation print theirs), and refuses a
host that shows none with it, naming the fingerprints it did show. Noust ships no fingerprints of
its own: a list in the code goes stale the day a code host rotates a key. A host pinned already is
never changed by it. The same from the terminal:
`noust app deploy-key show|list|generate|import|assign|rotate|test|remove|prune|hosts|pin-host|forget-host`.

A private https repository is reached with a user and a token kept in the secret store, not in
the source URL: `PUT /api/apps/{domain}/git-credential` (sudo mode, `{username, secret}`),
`GET` (never the token) and `DELETE` (sudo mode), or `noust app git-credential show|set|remove`.
For Bitbucket Cloud the user is your username or `x-bitbucket-api-token-auth` with an API token,
or `x-token-auth` with a repository or workspace access token.

## Health

`GET /health` answers `{"status": "healthy", "service": "noust-web"}` without authentication,
for load balancers and uptime checks. It says nothing about the machine; use
`GET /api/system/health` (authenticated) for that.

## Endpoint reference

Generated from the schema this release serves. Each row gives the permission every method
needs, "sudo" where a browser session must confirm first (`x-noust-requires-elevation`), and
"four-eyes" where the call becomes an approval request when approvals apply
(`x-noust-requires-approval`). `public` needs no credential; `self` any signed-in principal,
acting on its own things. Request and response bodies are in `/api/openapi.json`.

On a central, every node's API is also reachable through the central at
`/api/nodes/{node}/api/...`, its event stream at `/api/nodes/{node}/events` and its WebSockets
at `/ws/nodes/{node}/...`; the node applies its own permissions and its ceiling for the central
(see [CENTRAL.md](CENTRAL.md)).

### Applications

| Endpoint | Method, permission | |
|---|---|---|
| `/api/apps` | GET `apps.read`; POST `apps.manage`, four-eyes | List; deploy a new application (job). A local path needs sudo mode |
| `/api/apps/adopt` | POST `apps.manage`, sudo | Register a Docker Compose stack that already runs, without touching it; `preview: true` answers what it would record. `409` with Compose's dry run when starting it would recreate something |
| `/api/apps/import` | POST `apps.manage`, sudo |  |
| `/api/apps/inspect` | POST `apps.manage` | Preview a source without deploying. A local path needs sudo mode |
| `/api/apps/types` | GET `apps.read` |  |
| `/api/apps/{domain}` | GET `apps.read`; DELETE `apps.manage`, sudo | Delete (job). An adopted stack's directory, outside the apps directory, is kept unless `remove_adopted_directory` names it |
| `/api/apps/{domain}/backup-before-update` | PATCH `apps.manage`, sudo | `{enabled}`: whether an update of a Compose stack dumps its databases first |
| `/api/apps/{domain}/branch` | PATCH `apps.manage`, sudo | Pin the branch it deploys from, or unpin it |
| `/api/apps/{domain}/clone` | POST `apps.manage`, sudo | `{domain, team, project, environment, database, source}`: another app with this one's settings, variables, hooks, tags and previews, deployed from the same source (job). `database` is `empty` or `share` and is required when the app has one ([teams.md](teams.md)) |
| `/api/apps/{domain}/databases` | GET `databases.read`; POST `databases.write` |  |
| `/api/apps/{domain}/databases/link` | POST `databases.write` |  |
| `/api/apps/{domain}/databases/{engine}/{name}` | DELETE `databases.manage` |  |
| `/api/apps/{domain}/databases/{engine}/{name}/url` | POST `secrets.reveal`, sudo |  |
| `/api/apps/{domain}/deployments/{deployment_id}/rebuild` | POST `apps.deploy` |  |
| `/api/apps/{domain}/deployments/{deployment_id}/rollback` | POST `apps.deploy` | Go back to what a deployment produced (job). `409 schema_changed` unless `{"schema_changed_ok": true}` |
| `/api/apps/{domain}/diagnose` | GET `apps.read` |  |
| `/api/apps/{domain}/domains` | GET `apps.read`; POST `apps.manage` | Aliases and redirects, also for Compose and monorepo applications since 3.2 |
| `/api/apps/{domain}/domains/{name}` | DELETE `apps.manage`, sudo |  |
| `/api/apps/{domain}/domains/{name}/dns` | GET `apps.read` |  |
| `/api/apps/{domain}/env` | GET `apps.read`; PUT `apps.manage`, sudo | `.env`, redacted (`?unmask=true` needs `secrets.reveal` and sudo mode); replace it |
| `/api/apps/{domain}/env/marks` | PUT `apps.manage`, sudo |  |
| `/api/apps/{domain}/env/resolve` | POST `apps.manage`, sudo | Resolve the `{{...}}` references of its variables again without a deploy; answers which variables changed |
| `/api/apps/{domain}/export` | GET `secrets.reveal` |  |
| `/api/apps/{domain}/follow-tags` | PATCH `apps.manage`, sudo | `{pattern}`: deploy the newest git tag that matches a glob (`v*`) instead of a branch; null follows a branch again |
| `/api/apps/{domain}/headless` | GET `apps.read`; POST `apps.manage`, sudo | Whether a Compose stack publishes no port; record it as a worker (`{remove_site}` also retires the site Noust wrote) |
| `/api/apps/{domain}/health` | PATCH `apps.manage`, sudo | `{path, expect, timeout}` of the health gate; null is the default |
| `/api/databases/public` | GET `databases.read`; POST `databases.manage`, sudo | The databases reachable from the network; make one reachable (`{engine, database, port, bind_address, sources, tls, profile, confirm}`): a public port, an account of its own, TLS where the engine can, the firewall; the password is in the answer, once |
| `/api/databases/public/plan` | POST `databases.read` | What opening would do and what the operator accepts; changes nothing |
| `/api/databases/public/{engine}/{database}` | GET `databases.read`; DELETE `databases.manage`, sudo | Whether a database is public and how; make it private again (undoes all of it) |
| `/api/databases/public/{engine}/{database}/reveal` | POST `secrets.reveal`, sudo | The connection details with the account's password |
| `/api/databases/provisioning/choices` | GET `databases.read` | Each engine, the version that runs and the versions a database can be created on (`installed`, `installable`, `unavailable`) |
| `/api/databases/instances` | GET `databases.read`; POST `databases.manage`, sudo | The engines Noust runs itself in containers beside the host's (`name`, `key`, `reference`, `host_port`, `memory_limit_mb`, `state`, `status`) and the memory they hold; make one (`{name, engine, version, memory_mb, port}`, a job): the official image pinned by digest, on 127.0.0.1 only |
| `/api/databases/instances/catalog` | GET `databases.read` | What can be run as an instance: each engine, its versions and the digest pinned for each |
| `/api/databases/instances/plan` | POST `databases.read` | What making an instance would do; changes nothing |
| `/api/databases/instances/{name}` | GET `databases.read`; DELETE `databases.manage`, sudo | Describe one; remove it with its data (`?confirm=true`, a job; refused while an application uses it) |
| `/api/databases/instances/{name}/start`, `.../stop` | POST `apps.operate` | Start it (a job that returns once the engine answers) and stop it; its data stays |
| `/api/databases/instances/{name}/upgrade` | POST `databases.manage`, sudo | Move it to the newest image of its version (`?backup=false` skips the dumps taken first; a job); a failed gate puts the previous image back |
| `/api/databases/instances/{name}/remove-plan` | GET `databases.read` | What removing it deletes and what stops it |
| `/api/apps/{domain}/deploy-key` | GET `apps.read`; POST `apps.manage`; DELETE `apps.manage`, sudo | The key the repository is reached with; make one (or assign `{key}`); take the repository off it |
| `/api/apps/{domain}/deploy-key/import` | POST `apps.manage`, sudo | Use a private key you have (`{private_key, name}`) |
| `/api/apps/{domain}/deploy-key/rotate` | POST `apps.manage`; DELETE `apps.manage` | Make the key that replaces it, keeping the old working; give the rotation up |
| `/api/apps/{domain}/deploy-key/test` | POST `apps.manage` | Ask the repository with the key, as a deploy would; a waiting key that works becomes the key |
| `/api/apps/{domain}/git-credential` | GET `apps.read`; PUT `apps.manage`, sudo; DELETE `apps.manage`, sudo | The https user and token of a private repository, as a secret; the token is never returned |
| `/api/deploy-keys` | GET `apps.read` | Every deploy key and the applications it reaches |
| `/api/deploy-keys/hosts` | GET `apps.read` | The SSH host keys pinned the first time each code host was reached |
| `/api/deploy-keys/hosts/{host}` | DELETE `server.host_access`, sudo | Forget a pinned host key, to accept the one the host shows now |
| `/api/apps/{domain}/hooks` | GET `apps.read`; PUT `root_equivalent`, sudo, four-eyes; DELETE `apps.manage` | The hooks the next deployment runs and where they come from; set the operator's own (`{document}`, a YAML `hooks:`), which replace the repository's `noust.yaml`; clear them |
| `/api/apps/{domain}/identity` | GET `apps.read` | The system account it runs as, whether it is its own, and why not when it cannot be |
| `/api/apps/{domain}/identity/migrate` | POST `root_equivalent`, sudo, four-eyes | Move it to its own account behind the health gate (job) |
| `/api/apps/{domain}/limits` | PATCH `apps.manage`, sudo | `{memory_max_mb, cpu_quota_percent, tasks_max, restart}`; null removes a limit |
| `/api/apps/{domain}/logs` | GET `apps.read` |  |
| `/api/apps/{domain}/metrics` | GET `apps.read` |  |
| `/api/apps/{domain}/migrate` | POST `apps.manage`, sudo |  |
| `/api/apps/{domain}/migrate/plan` | GET `apps.read` |  |
| `/api/apps/{domain}/previews` | GET `apps.read`; POST `apps.deploy`, sudo | The settings and the previews (`has_database` says whether there is a database to isolate); make one by hand from exactly one of `{pull_request}`, `{branch}` or `{tag}` (`allow_fork` for a pull request from a fork). 202 with the preview and its job ([previews.md](previews.md)) |
| `/api/apps/{domain}/previews/pull-requests` | GET `apps.read` | The open pull requests of the repository, each with the preview it has. Needs the GitHub App; `502` says how to go on without it |
| `/api/apps/{domain}/previews/settings` | PUT `apps.manage`, sudo; DELETE `apps.manage`, sudo | `inherit_env` (`allowlist` or `all`), `allow_env`, `exclude_env` and `data_mode` (`empty` or `shared`) say what a preview is given |
| `/api/apps/{domain}/previews/variables` | GET `apps.read` (`?unmask=true`: `admin`, sudo, audited) | The variables defined for previews alone, secrets masked |
| `/api/apps/{domain}/previews/variables/{name}` | PUT `apps.manage`, sudo; DELETE `apps.manage`, sudo | `{value, secret, audience}` |
| `/api/apps/{domain}/previews/{number}` | GET `apps.read`; DELETE `apps.manage`, sudo | One preview, whether its unit runs and its last deployment; remove it with its application, volumes and databases (job) |
| `/api/apps/{domain}/previews/{number}/logs` | GET `apps.read` | The journal's tail and the log of its last build, verbatim |
| `/api/apps/{domain}/previews/{number}/redeploy` | POST `apps.deploy`, sudo | Build it again from its branch as it is now (job) |
| `/api/apps/{domain}/previews/{number}/start` | POST `apps.operate`, sudo | Start a stopped preview |
| `/api/apps/{domain}/previews/{number}/stop` | POST `apps.operate`, sudo | Stop it without removing it; it still counts toward the limit |
| `/api/apps/{domain}/promote` | POST `apps.deploy`, sudo | `{target, release, schema_changed_ok}`: put a release on the same app in another environment of its project, without building it again (job) ([teams.md](teams.md)) |
| `/api/apps/{domain}/promotion` | GET `apps.read` | The release that serves, and the apps it can be promoted to |
| `/api/apps/{domain}/releases` | GET `apps.read` |  |
| `/api/apps/{domain}/releases/retention` | PATCH `apps.manage`, sudo | `{keep}`, 1 to 50; prunes now |
| `/api/apps/{domain}/releases/{release_id}/activate` | POST `apps.deploy` | Instant rollback or roll forward, behind the health gate. `409 schema_changed` unless `?schema_changed_ok=true` |
| `/api/apps/{domain}/restart` | POST `apps.operate` |  |
| `/api/apps/{domain}/rollback-points` | GET `apps.read` |  |
| `/api/apps/{domain}/sandbox` | GET `apps.read` | How it builds: sandbox or root, and why |
| `/api/apps/{domain}/sandbox/compose-exception` | PUT `root_equivalent`, sudo, four-eyes; DELETE `apps.manage` |  |
| `/api/apps/{domain}/sandbox/disable` | POST `root_equivalent`, sudo, four-eyes |  |
| `/api/apps/{domain}/sandbox/enable` | POST `apps.manage` |  |
| `/api/apps/{domain}/sandbox/test` | POST `apps.deploy` | Build the live commit in the sandbox, activating nothing (job) |
| `/api/apps/{domain}/site-logs` | GET `apps.read` | The end of the web server's access or error log for the site (`kind`, `lines`, `contains`), secrets masked; nothing written yet answers `available: false` with the path it looked for |
| `/api/apps/{domain}/start` | POST `apps.operate` |  |
| `/api/apps/{domain}/stop` | POST `apps.operate` |  |
| `/api/apps/{domain}/tags` | GET `apps.read`; PUT `apps.manage` | The tags it carries (`{tags}`, at most 16); replace them |
| `/api/apps/{domain}/webhook` | GET `apps.read` | How far the deploy webhook is set up |
| `/api/apps/{domain}/webhook-secret` | POST `apps.manage`, sudo; DELETE `apps.manage`, sudo | Create or rotate the secret (shown once); disable the webhook |
| `/api/apps/{domain}/webhook/deliveries` | GET `apps.read` | Deployments webhooks triggered |
| `/api/apps/{domain}/webhook/received` | GET `apps.read` | Every delivery received, ignored and refused ones included |
| `/api/apps/{domain}/webhook/reveal` | POST `secrets.reveal`, sudo |  |
| `/api/apps/{domain}/zero-downtime` | GET `apps.read`; PUT `apps.manage`, sudo | `{enabled, drain_seconds}`; a Docker Compose stack is accepted since 3.2, updated behind relays |

### Sources, builds and imports

Added in 3.4. Each is documented where its feature is: [sources.md](sources.md), [ci.md](ci.md),
[build.md](build.md), [declarative.md](declarative.md), [workspaces.md](workspaces.md),
[containers.md](containers.md#registry-accounts) and [compose.md](compose.md).

| Endpoint | Method, permission | |
|---|---|---|
| `/api/deploy` | POST `apps.deploy` | Deploy up to 20 applications with one call (a pipeline's token); `{apps, force, no_cache}` |
| `/api/apps/{domain}/deploy` | POST `apps.deploy` | Deploy one: `{force, no_cache, commit, tag, image_tag, pull_request}`; `queued`, `joined`, `nothing_new` or `429` when the queue is full |
| `/api/apps/{domain}/source-options` | GET `apps.read`; PUT, DELETE `apps.manage` | Submodules, Git LFS, shallow clone, watch paths and skip markers |
| `/api/apps/{domain}/build-cache` | GET `apps.read`; DELETE `apps.deploy` | Size of the build cache; empty it |
| `/api/apps/{domain}/process-types` | GET `apps.read` | The process types the code declares and which a deployment uses |
| `/api/apps/{domain}/compose` | GET `apps.read`; PUT, DELETE `apps.manage`, sudo | A Compose stack's file, profiles and extra `build` / `up` arguments |
| `/api/apps/{domain}/declaration` | GET `secrets.reveal` | The application as a `noust.yaml` declaration (no secret variables) |
| `/api/apps/{domain}/apply` | POST `root_equivalent`, sudo | `{declaration, check, confirm, prune}`: the plan, and what was applied |
| `/api/apps/{domain}/registry-logins` | GET `apps.read`; PUT `apps.manage`, sudo | The registry accounts an application pulls and builds with |
| `/api/registries` | GET `apps.read`; POST `apps.manage`, sudo | Registry accounts (Docker Hub, GHCR, GitLab, ECR, ACR, Artifact Registry, any host); the password is never returned |
| `/api/registries/{name}` | GET `apps.read`; PATCH, DELETE `apps.manage`, sudo | |
| `/api/registries/{name}/test` | POST `apps.manage`, sudo | Try the account in a private Docker configuration |
| `/api/import/applications` | POST `apps.manage`, sudo | List the applications of a Coolify or Dokploy server, read with a token |
| `/api/import/proposal` | POST `apps.manage`, sudo | One of them as an export document, ready for `POST /api/apps/import` |
| `/api/workspaces/inspect` | POST `apps.manage` | The workspaces of a monorepo and which are applications |
| `/api/workspaces/deploy` | POST `apps.manage`, sudo | One application per workspace named, each a job |
| `/api/integrations/github/host` | PUT `apps.manage`, sudo | The GitHub the App talks to (github.com or an Enterprise host) |
| `/api/integrations/gitlab` | GET `apps.read` | GitLab connections, OAuth applications, and the redirect URI |
| `/api/integrations/gitlab/connections` | POST `apps.manage`, sudo | Add a connection with an access token (checked first, never returned) |
| `/api/integrations/gitlab/connections/{name}` | DELETE `apps.manage`, sudo | |
| `/api/integrations/gitlab/connections/{name}/test` | POST `apps.deploy` | Ask GitLab who it is |
| `/api/integrations/gitlab/connections/{name}/groups` | GET `apps.read` | The picker: groups |
| `/api/integrations/gitlab/connections/{name}/projects` | GET `apps.read` | The picker: projects, `?group=&search=` |
| `/api/integrations/gitlab/connections/{name}/projects/{project}/branches` | GET `apps.read` | The picker: branches of a project |
| `/api/integrations/gitlab/oauth-apps` | PUT `apps.manage`, sudo | Record the OAuth application registered in GitLab |
| `/api/integrations/gitlab/oauth-apps/{host}` | DELETE `apps.manage`, sudo | |
| `/api/integrations/gitlab/oauth/authorize` | POST `apps.manage`, sudo | Begin signing in: the URL to send the browser to |
| `/api/integrations/gitlab/oauth/callback` | POST `apps.manage`, sudo | Finish: `{code, state}` |

### Teams, projects and environments

| Endpoint | Method, permission | |
|---|---|---|
| `/api/teams` | GET `teams.read`; POST `teams.manage` | The teams you see; create one (an administrator) |
| `/api/teams/mine` | GET `self` | Your teams with your role in each, your active team, whether you see them all |
| `/api/teams/active` | PUT `self` | `{team}`: the team you work in, where new things go when a request names none |
| `/api/teams/roles` | GET `self` | What each team role holds |
| `/api/teams/{team}` | GET `teams.read`; PATCH `teams.manage`; DELETE `teams.manage`, sudo | One team, with what it holds; rename; delete an empty one |
| `/api/teams/{team}/members` | GET `teams.read`; POST `teams.manage`, sudo | `{username, role}`: an account that exists |
| `/api/teams/{team}/members/{username}` | PATCH `teams.manage`, sudo; DELETE `teams.manage`, sudo | Change its role; remove it. The last owner stays |
| `/api/teams/{team}/projects` | GET `teams.read`; POST `teams.manage` | A project starts with its production environment |
| `/api/teams/{team}/projects/{project}` | DELETE `teams.manage`, sudo | Only an empty one |
| `/api/teams/{team}/projects/{project}/environments` | GET `teams.read`; POST `teams.manage` |  |
| `/api/teams/{team}/projects/{project}/environments/{environment}` | DELETE `teams.manage`, sudo | Not the last of its project |
| `/api/teams/{team}/variables` | GET `apps.read` (`?unmask=true`: `admin`, sudo, audited); POST `apps.manage`, sudo | A team's shared variables, secrets masked; create or change several (`{variables: {NAME: {value, secret, audience}}}`) ([variables.md](variables.md)) |
| `/api/teams/{team}/variables/{name}` | PUT `apps.manage`, sudo; DELETE `apps.manage`, sudo | `{value, secret, audience}`; the delete names the applications that still refer to it |
| `/api/teams/{team}/variables/{name}/usage` | GET `apps.read` | The applications you see whose variables refer to it |
| `/api/teams/{team}/projects/{project}/variables` | GET `apps.read`; POST `apps.manage`, sudo | The same, for a project |
| `/api/teams/{team}/projects/{project}/variables/{name}` | PUT `apps.manage`, sudo; DELETE `apps.manage`, sudo |  |
| `/api/teams/{team}/projects/{project}/variables/{name}/usage` | GET `apps.read` |  |
| `/api/teams/{team}/projects/{project}/environments/{environment}/variables` | GET `apps.read`; POST `apps.manage`, sudo | The same, for an environment |
| `/api/teams/{team}/projects/{project}/environments/{environment}/variables/{name}` | PUT `apps.manage`, sudo; DELETE `apps.manage`, sudo |  |
| `/api/teams/{team}/projects/{project}/environments/{environment}/variables/{name}/usage` | GET `apps.read` |  |
| `/api/projects` | GET `teams.read` | Every project you see; `?team=` |
| `/api/environments` | GET `teams.read` | Every environment you see; `?team=&project=` |
| `/api/apps/{domain}/move` | POST `apps.manage`, sudo | `{team, project, environment}`: with its sites, databases and backup schedule. Needs `apps.manage` in the team it goes to as well |

Lists of applications, sites, databases and backup schedules take `team`, `project` and
`environment`; creating one takes the same three.

### Jobs and deployments

| Endpoint | Method, permission | |
|---|---|---|
| `/api/deployments` | GET `apps.read` |  |
| `/api/deployments/{deployment_id}` | GET `apps.read` |  |
| `/api/deployments/{deployment_id}/log` | GET `apps.read` |  |
| `/api/jobs` | GET `apps.read` |  |
| `/api/jobs/active` | GET `apps.read` |  |
| `/api/jobs/backup` | POST `backups.run` |  |
| `/api/jobs/cert` | POST `apps.manage` |  |
| `/api/jobs/cleanup` | DELETE `audit.manage` | Forget finished jobs in memory; the history stays |
| `/api/jobs/queue` | GET `apps.read` | What runs, what waits and the limits: `{running, queued, builds_running, max_concurrent_builds, queue_limit, builds_queued}` |
| `/api/jobs/delete` | POST `apps.manage`, sudo | `{domain, remove_files, remove_ssl, remove_adopted_directory}` |
| `/api/jobs/rollback` | POST `apps.deploy` | `{domain, backup_id, schema_changed_ok}`: restore a backup. `409 schema_changed` unless confirmed |
| `/api/jobs/update` | POST `apps.deploy` | `{domain, force}` |
| `/api/jobs/{job_id}` | GET `apps.read` |  |
| `/api/jobs/{job_id}/cancel` | POST `apps.operate` | A job that waits, or a deploy or an update that is building. `409 cancel_refused` once its release is being activated |
| `/api/jobs/{job_id}/log` | GET `apps.read` |  |

### Sites, certificates and domains

| Endpoint | Method, permission | |
|---|---|---|
| `/api/certs` | GET `apps.read` |  |
| `/api/certs/renew-all` | POST `apps.operate` |  |
| `/api/certs/{domain}` | GET `apps.read`; POST `apps.manage`; DELETE `apps.manage`, sudo |  |
| `/api/certs/{domain}/renew` | POST `apps.operate` |  |
| `/api/certs/{domain}/revoke` | POST `apps.manage`, sudo |  |
| `/api/domains/dns` | GET `apps.read` |  |
| `/api/sites` | GET `apps.read`; POST `apps.manage` | Every site file in the web server's directory together with the store's: `noust_managed`, `app` and `site_name` on each |
| `/api/sites/reload` | POST `apps.operate` |  |
| `/api/sites/templates` | GET `apps.read` |  |
| `/api/sites/{domain}` | GET `apps.read`; DELETE `apps.manage`, sudo |  |
| `/api/sites/{domain}/config` | GET `apps.read`; PUT `root_equivalent`, sudo, four-eyes |  |
| `/api/sites/{domain}/config/edit` | POST `apps.read` | Apply edit operations to a text (`{config, ops}`); answers the new text, its structure and `changed_lines`; writes nothing |
| `/api/sites/{domain}/config/test` | POST `apps.operate` | Test a candidate inside the live `nginx.conf` or `apache2.conf` |
| `/api/sites/{domain}/disable` | POST `apps.manage` |  |
| `/api/sites/{domain}/enable` | POST `apps.manage` |  |
| `/api/sites/{domain}/route` | POST `apps.read` | Which server and location answer `{host, path, scheme}` and why; a draft in `config` or the saved file |
| `/api/sites/{domain}/structure` | GET `apps.read`; POST `apps.read` | The structured model of the saved file; of a draft (`{config}`, writes nothing) |
| `/api/sites/{domain}/topology` | GET `apps.read` | The structure plus what is behind it now: who holds each port, whether it answers, certificate expiry |

### Services and cron

| Endpoint | Method, permission | |
|---|---|---|
| `/api/cron` | GET `apps.read`; POST `root_equivalent`, sudo, four-eyes |  |
| `/api/cron/preview` | POST `apps.read` |  |
| `/api/cron/{name}` | DELETE `apps.manage`, sudo |  |
| `/api/cron/{name}/disable` | POST `apps.operate` |  |
| `/api/cron/{name}/enable` | POST `apps.operate` |  |
| `/api/cron/{name}/run` | POST `apps.operate` |  |
| `/api/cron/{name}/runs` | GET `apps.read` |  |
| `/api/services` | GET `server.read`; POST `root_equivalent`, sudo, four-eyes |  |
| `/api/services/verify` | POST `root_equivalent` |  |
| `/api/services/{name}` | GET `server.read`; DELETE `server.manage`, sudo |  |
| `/api/services/{name}/config` | GET `secrets.reveal`; PUT `root_equivalent`, sudo, four-eyes |  |
| `/api/services/{name}/disable` | POST `apps.operate` |  |
| `/api/services/{name}/enable` | POST `apps.operate` |  |
| `/api/services/{name}/logs` | GET `server.read` |  |
| `/api/services/{name}/restart` | POST `apps.operate` |  |
| `/api/services/{name}/start` | POST `apps.operate` |  |
| `/api/services/{name}/stop` | POST `apps.operate` |  |
| `/api/services/{name}/tags` | GET `server.read`; PUT `server.manage` | The unit's tags; `{tags}` makes it carry exactly those. `GET /api/services?tag=` filters the list |

### Backups

| Endpoint | Method, permission | |
|---|---|---|
| `/api/backup-destinations` | GET `backups.read`; POST `backups.manage`, sudo |  |
| `/api/backup-destinations/backends` | GET `backups.read` |  |
| `/api/backup-destinations/{name}` | PUT `backups.manage`, sudo; DELETE `backups.manage`, sudo |  |
| `/api/backup-destinations/{name}/backups` | GET `backups.read` |  |
| `/api/backup-destinations/{name}/backups/{backup_id}/restore` | POST `backups.manage`, sudo |  |
| `/api/backup-destinations/{name}/show-key` | POST `secrets.reveal`, sudo |  |
| `/api/backup-destinations/{name}/test` | POST `backups.run` |  |
| `/api/backup-schedules` | GET `backups.read`; POST `root_equivalent`, sudo, four-eyes |  |
| `/api/backup-schedules/{domain}` | PUT `root_equivalent`, sudo, four-eyes; DELETE `backups.manage`, sudo |  |
| `/api/backups` | GET `backups.read`; POST `backups.run` |  |
| `/api/backups/storage` | GET `backups.read` |  |
| `/api/backups/{backup_id}` | GET `backups.read`; DELETE `backups.manage`, sudo |  |
| `/api/backups/{backup_id}/push` | POST `backups.manage`, sudo |  |
| `/api/backups/{backup_id}/restore` | POST `backups.manage`, sudo | Restore (job). Putting back only the files past a deployment that changed the schema is `409 schema_changed` unless `schema_changed_ok` |
| `/api/backups/{backup_id}/verify` | POST `backups.run` |  |

### Databases

| Endpoint | Method, permission | |
|---|---|---|
| `/api/databases/backup-policies` | GET `backups.read` |  |
| `/api/databases/backup-policies/{engine}/{database}` | GET `backups.read`; PUT `backups.manage`, sudo; DELETE `backups.manage`, sudo |  |
| `/api/databases/backup-policies/{engine}/{database}/run` | POST `backups.run` |  |
| `/api/databases/backups` | GET `databases.read`; POST `backups.run` |  |
| `/api/databases/backups/remote` | GET `backups.read` |  |
| `/api/databases/backups/remote/databases` | GET `backups.read` |  |
| `/api/databases/backups/restore` | POST `databases.manage`, sudo | Restore over a database after a safety copy, or `as_new` (job) |
| `/api/databases/backups/restore-remote` | POST `databases.manage`, sudo |  |
| `/api/databases/backups/suggest-name` | GET `databases.read` |  |
| `/api/databases/backups/{name}` | DELETE `backups.manage`, sudo |  |
| `/api/databases/backups/{name}/download` | GET `databases.manage`, sudo |  |
| `/api/databases/backups/{name}/push` | POST `backups.manage` |  |
| `/api/databases/backups/{name}/verify` | POST `backups.run` |  |
| `/api/databases/connection-string` | POST `secrets.reveal` |  |
| `/api/databases/console/history` | GET `databases.read`; DELETE `databases.read` |  |
| `/api/databases/console/saved` | GET `databases.read`; POST `databases.read` |  |
| `/api/databases/console/saved/{saved_id}` | PUT `databases.read`; DELETE `databases.read` |  |
| `/api/databases/databases` | GET `databases.read`; POST `databases.write` |  |
| `/api/databases/databases/adopt` | POST `databases.write` |  |
| `/api/databases/databases/{engine}/{name}` | GET `databases.read`; DELETE `databases.manage`, sudo |  |
| `/api/databases/databases/{engine}/{name}/access` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/access/{username}` | PUT `databases.write` |  |
| `/api/databases/databases/{engine}/{name}/connect` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/fix-owner` | GET `databases.read`; POST `databases.manage`, sudo |  |
| `/api/databases/databases/{engine}/{name}/forget` | POST `databases.manage`, sudo |  |
| `/api/databases/databases/{engine}/{name}/key` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/keys` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/metrics` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/overview` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/tags` | GET `databases.read`; PUT `databases.write` | The database's tags; `{tags}` makes it carry exactly those. `GET /api/databases/databases?tag=` filters the list, and every database carries its `tags` |
| `/api/databases/databases/{engine}/{name}/relation` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/relations` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/rows` | GET `databases.read`; POST `databases.write`, sudo, four-eyes; PATCH `databases.write`, sudo, four-eyes |  |
| `/api/databases/databases/{engine}/{name}/rows/delete` | POST `databases.write`, sudo, four-eyes |  |
| `/api/databases/databases/{engine}/{name}/schemas` | GET `databases.read` |  |
| `/api/databases/databases/{engine}/{name}/slow-queries` | GET `databases.read` |  |
| `/api/databases/engines` | GET `databases.read` |  |
| `/api/databases/engines/{engine}/exposure` | GET `databases.read` |  |
| `/api/databases/engines/{engine}/install` | POST `databases.manage` |  |
| `/api/databases/engines/{engine}/logs` | GET `databases.read` |  |
| `/api/databases/engines/{engine}/metrics` | GET `databases.read` |  |
| `/api/databases/engines/{engine}/privileges` | GET `databases.read` |  |
| `/api/databases/engines/{engine}/restart` | POST `apps.operate` |  |
| `/api/databases/engines/{engine}/start` | POST `apps.operate` |  |
| `/api/databases/engines/{engine}/status` | GET `databases.read` |  |
| `/api/databases/engines/{engine}/stop` | POST `apps.operate` |  |
| `/api/databases/engines/{engine}/uninstall` | POST `databases.manage`, sudo |  |
| `/api/databases/exposure` | GET `databases.read` |  |
| `/api/databases/provisioning/plan` | GET `databases.read` |  |
| `/api/databases/query` | POST `databases.read`, four-eyes | One statement; `mode: "write"` needs `databases.write` and sudo mode (and a four-eyes approval when approvals apply) |
| `/api/databases/query/explain` | POST `databases.read`, four-eyes |  |
| `/api/databases/query/export` | POST `databases.read` |  |
| `/api/databases/users` | POST `databases.write` |  |
| `/api/databases/users/grant` | POST `databases.write` |  |
| `/api/databases/users/revoke` | POST `databases.write` |  |
| `/api/databases/users/{engine}` | GET `databases.read` |  |
| `/api/databases/users/{engine}/{username}` | DELETE `databases.manage`, sudo |  |
| `/api/databases/users/{engine}/{username}/password` | POST `databases.manage`, sudo |  |
| `/api/databases/users/{engine}/{username}/password/reveal` | POST `secrets.reveal`, sudo |  |

### The server

| Endpoint | Method, permission | |
|---|---|---|
| `/api/alerts/rules` | GET `server.read` | The alert rules, where each subject of each one stands (`firing` or `ok`), and the metrics a rule may watch |
| `/api/alerts/rules/{name}` | PUT `server.manage`; DELETE `server.manage` | Create or replace a rule (a limit as a number or as text such as `90%` or `512MB`); replacing it forgets what the old limit had decided |
| `/api/alerts/rules/{name}/disable` | POST `server.manage` |  |
| `/api/alerts/rules/{name}/enable` | POST `server.manage` |  |
| `/api/console/exposure` | GET `server.read`; POST `server.host_access`, sudo, four-eyes; DELETE `server.host_access`, sudo | Whether the console is published on a name of its own over TLS; publish it, or take it off (each a job: the nginx site, the certificate, the console's trusted proxy and its restart, undone exactly). Refused for want of a second factor on every account, and under the ENS profile unless forced |
| `/api/console/exposure/preview` | POST `server.host_access` | The same checks, and the steps it would take, changing nothing |
| `/api/secret-providers` | GET `settings.read` | The secret managers variables can refer to (Vault, Doppler, Infisical); a token is never in the answer ([variables.md](variables.md)) |
| `/api/secret-providers/kinds` | GET `settings.read` | The kinds and the settings each takes |
| `/api/secret-providers/{name}` | PUT `settings.manage`, sudo; DELETE `settings.manage`, sudo | `{kind, address, settings, token, team}`; without a `token` the stored one stays |
| `/api/secret-providers/{name}/test` | POST `settings.manage`, sudo | Prove it answers and accepts its token |
| `/api/tags` | GET `apps.read` | The tags your applications carry, with those applications |
| `/api/tags/{tag}/deploy` | POST `apps.deploy` | Update every app that carries a tag, each as a job; `{force}` rebuilds those with nothing new. A pipeline's token calls this to redeploy a group |
| `/api/tags/{tag}/restart` | POST `apps.operate` | Restart every application and service that carries a tag, one by one; `{dry_run}` says what would happen and restarts nothing. Databases that carry it are listed and left. A caller reaches what it sees: a team its own applications, only the instance the server's units |
| `/api/variables/server` | GET `settings.read` (`?unmask=true`: `admin`, sudo, audited); POST `settings.manage`, sudo | The server's shared variables, which every application can refer to |
| `/api/variables/server/{name}` | PUT `settings.manage`, sudo; DELETE `settings.manage`, sudo |  |
| `/api/variables/server/{name}/usage` | GET `settings.read` |  |
| `/api/metrics` | GET `server.read` |  |
| `/api/metrics/query` | GET `server.read` | Several series in one request, at the resolution the window needs |
| `/api/metrics/{metric}` | GET `server.read` |  |
| `/api/monitor/config` | GET `server.read` |  |
| `/api/monitor/disable` | POST `server.manage` |  |
| `/api/monitor/enable` | POST `server.manage` |  |
| `/api/monitor/install` | POST `server.manage` |  |
| `/api/monitor/metrics` | GET `server.read` |  |
| `/api/monitor/observations` | GET `server.read` |  |
| `/api/monitor/observations/{observation_id}/acknowledge` | POST `apps.operate` |  |
| `/api/monitor/processes` | GET `server.read` |  |
| `/api/monitor/scan` | POST `apps.operate` |  |
| `/api/monitor/start` | POST `server.manage` |  |
| `/api/monitor/status` | GET `server.read` |  |
| `/api/monitor/stop` | POST `server.manage` |  |
| `/api/monitor/test-email` | POST `apps.operate` |  |
| `/api/monitor/uninstall` | POST `server.manage` |  |
| `/api/overview` | GET `server.read` | The Overview page in one answer |
| `/api/server/capabilities` | GET `server.read` |  |
| `/api/server/clock/timezones` | GET `server.read` | The time zones the managed server knows, with region, city, offset now and abbreviation |
| `/api/server/identity` | GET `server.read` |  |
| `/api/server/identity/hostname` | PUT `server.manage`, sudo |  |
| `/api/server/logs` | GET `secrets.reveal` | The journal of any unit |
| `/api/server/logs/boots` | GET `server.read` |  |
| `/api/server/logs/units` | GET `server.read` |  |
| `/api/server/power` | GET `server.read` |  |
| `/api/server/power/reboot` | POST `server.manage`, sudo |  |
| `/api/server/power/scheduled` | DELETE `server.manage` |  |
| `/api/server/power/shutdown` | POST `server.host_access`, sudo |  |
| `/api/server/processes` | GET `server.read` |  |
| `/api/server/security` | GET `server.read` |  |
| `/api/server/security/changes` | GET `server.read` |  |
| `/api/server/security/changes/{change_id}/confirm` | POST `server.host_access`, sudo |  |
| `/api/server/security/changes/{change_id}/revert` | POST `server.host_access`, sudo |  |
| `/api/server/security/checks` | GET `server.read` |  |
| `/api/server/security/checks/refresh` | POST `server.read` |  |
| `/api/server/security/checks/{check_id}/fix` | POST `server.host_access`, sudo |  |
| `/api/server/security/fail2ban` | GET `server.read` |  |
| `/api/server/security/fail2ban/install` | POST `server.manage`, sudo |  |
| `/api/server/security/fail2ban/unban` | POST `server.manage`, sudo |  |
| `/api/server/security/firewall` | GET `server.read` |  |
| `/api/server/security/firewall/disable` | POST `server.host_access`, sudo |  |
| `/api/server/security/firewall/enable` | POST `server.host_access`, sudo |  |
| `/api/server/security/firewall/rules` | POST `server.host_access`, sudo |  |
| `/api/server/security/firewall/rules/{rule_id}` | DELETE `server.host_access`, sudo |  |
| `/api/server/security/risks` | GET `server.read` |  |
| `/api/server/security/risks/{check_id}` | PUT `security.manage`, sudo; DELETE `security.manage`, sudo |  |
| `/api/server/security/ssh` | GET `server.read` |  |
| `/api/server/security/ssh/fixes/{fix}` | GET `server.read`; POST `server.host_access`, sudo |  |
| `/api/server/security/ssh/keys` | GET `server.read`; POST `server.host_access`, sudo |  |
| `/api/server/security/ssh/keys/remove` | POST `server.host_access`, sudo |  |
| `/api/server/storage` | GET `server.read` |  |
| `/api/server/storage/analyze` | POST `server.read` | Measure what takes space; it changes nothing, so a read-only credential may ask |
| `/api/server/storage/analyze/latest` | GET `server.read` |  |
| `/api/server/storage/cleanup` | POST `server.manage`, sudo |  |
| `/api/server/storage/cleanup/plan` | GET `server.read` |  |
| `/api/server/storage/docker/images` | GET `server.read` |  |
| `/api/server/storage/history` | GET `server.read` | Every cleanup that ran, by hand or by the timer: when, what started it, how full the fullest disk was before and after, what each action freed or removed and why one failed |
| `/api/server/storage/schedule` | GET `server.read`; PUT `server.manage`, sudo | The cleanup policy (schedule, threshold, actions) and its timer; setting it writes or removes the timer |
| `/api/server/storage/schedule/run` | POST `server.manage`, sudo | Run the policy now, as a job, whatever state the disks are in |
| `/api/server/summary` | GET `server.read` | What `noust server status` prints |
| `/api/server/swap` | GET `server.read`; POST `server.manage`, sudo; DELETE `server.manage`, sudo |  |
| `/api/server/swap/swappiness` | PUT `server.manage`, sudo |  |
| `/api/server/time` | GET `server.read`; PUT `server.manage`, sudo |  |
| `/api/server/updates` | GET `server.read` |  |
| `/api/server/updates/apply` | POST `server.manage`, sudo |  |
| `/api/server/updates/auto` | GET `server.read`; PUT `server.manage`, sudo |  |
| `/api/server/updates/plan` | GET `server.read` |  |
| `/api/server/updates/refresh` | POST `server.manage`, sudo |  |
| `/api/server/updates/repair` | POST `server.manage`, sudo |  |
| `/api/server/updates/restarts` | GET `server.read`; POST `server.manage`, sudo |  |
| `/api/server/updates/runs` | GET `server.read` |  |
| `/api/server/updates/runs/{update_id}` | GET `server.read` |  |
| `/api/system` | GET `server.read` |  |
| `/api/system/cpu` | GET `server.read` |  |
| `/api/system/disks` | GET `server.read` |  |
| `/api/system/health` | GET `server.read` |  |
| `/api/system/machine` | GET `server.read` |  |
| `/api/system/memory` | GET `server.read` |  |
| `/api/system/network` | GET `server.read` |  |
| `/api/system/processes` | GET `server.read` |  |
| `/api/system/update` | GET `server.read`; POST `server.manage`, sudo | Noust's own version, and updating it |
| `/api/system/version` | GET `server.read` |  |
| `/api/timeline` | GET `server.read` | Everything that happened in a stretch (journal, audit, deployments, jobs, what the monitor saw) with the busiest processes per minute; each source keeps its own permission |

### The fleet

| Endpoint | Method, permission | |
|---|---|---|
| `/api/central/unlock` | POST `security.manage`, sudo | Give a sealed central its passphrase |
| `/api/fleet/actions` | GET `fleet.read`; POST `fleet.manage` | Actions a bulk job can run; start one (`plan: true` only shows the plan) |
| `/api/fleet/activity` | GET `fleet.read` |  |
| `/api/fleet/apps` | GET `fleet.read` |  |
| `/api/fleet/backups` | GET `fleet.read` |  |
| `/api/fleet/certificates` | GET `fleet.read` |  |
| `/api/fleet/cloud/accounts` | GET `fleet.manage`; POST `fleet.manage`, sudo, four-eyes | The provider accounts (Hetzner, DigitalOcean, Vultr) this central creates servers with; adding one checks its token with the provider and keeps it as a secret. The token is left out of the approval's fingerprint and of every response (only its last four characters are shown) |
| `/api/fleet/cloud/accounts/{name}` | DELETE `fleet.manage`, sudo | Remove an account and its token (`force` when servers made with it are still on record) |
| `/api/fleet/cloud/accounts/{name}/catalog` | GET `fleet.manage` | What the account can create now, asked of the provider: locations, sizes (with processor and monthly price), images, SSH keys, firewalls, networks |
| `/api/fleet/cloud/accounts/{name}/token` | PUT `fleet.manage`, sudo, four-eyes | Replace an account's token, checked with the provider first |
| `/api/fleet/cloud/accounts/{name}/verify` | POST `fleet.manage` | Ask the provider whether it still accepts the token |
| `/api/fleet/cloud/providers` | GET `fleet.manage` | The providers Noust can create servers at, with where their tokens are made |
| `/api/fleet/cloud/scripts` | GET `fleet.manage` | The cloud-init library a new server can boot with (three come with Noust) |
| `/api/fleet/cloud/scripts/preview` | POST `fleet.manage` | The whole document a server would boot with, with its SSH host key withheld |
| `/api/fleet/cloud/scripts/{name}` | GET `fleet.manage`; PUT `fleet.manage`, sudo, four-eyes; DELETE `fleet.manage`, sudo | One library entry; saving checks it (what Noust owns cannot be overridden, a script says what runs it, nothing may carry a credential) |
| `/api/fleet/cloud/servers` | GET `fleet.manage`; POST `fleet.manage`, sudo, four-eyes | The machines this central created; create one (a job: the machine is made, waited for, Noust installed and the server joined; it costs money until deleted) |
| `/api/fleet/cloud/servers/{node}` | GET `fleet.manage`; DELETE `fleet.manage`, sudo, four-eyes | One machine (`live` asks the provider); delete it at the provider (`confirm` is its name) |
| `/api/fleet/cloud/servers/{node}/link` | POST `fleet.manage`, sudo; DELETE `fleet.manage`, sudo | Link a server already in the fleet to its machine at a provider (`{account, provider_id, force}`): the provider is asked whether the machine exists and is at the server's address, and nothing is changed there; DELETE forgets the link and touches neither the machine nor the server |
| `/api/fleet/cloud/servers/{node}/power-on` | POST `fleet.manage`, sudo | Power a machine on at the provider |
| `/api/fleet/enroll` | POST `fleet.manage`, sudo, four-eyes | Enroll a server with an address and a credential used once and never kept: a job that installs Noust there if it is missing, authorizes this central and joins it. The credential is left out of the approval's fingerprint |
| `/api/fleet/enroll/host-key` | POST `fleet.manage` | Ask an address which ed25519 host key answers, for the operator to confirm before anything is sent |
| `/api/fleet/jobs` | GET `fleet.read` |  |
| `/api/fleet/jobs/{job_id}` | GET `fleet.read` |  |
| `/api/fleet/jobs/{job_id}/retry` | POST `fleet.manage` |  |
| `/api/fleet/link` | GET `fleet.manage` | Whether this central takes servers that dial in (behind NAT or a CGNAT), what it lacks and the command that fixes it, and which servers dial in or were invited |
| `/api/fleet/link/accept` | POST `fleet.manage`, sudo, four-eyes | Register a server from the link code it printed, once it connects and answers with its token; nothing is kept otherwise |
| `/api/fleet/link/invites` | POST `fleet.manage`, sudo | Invite a server to dial in: gives it a loopback port of this central and the command to run on it, with this central's SSH host key |
| `/api/fleet/link/invites/{name}` | DELETE `fleet.manage`, sudo | Cancel an invitation nobody answered |
| `/api/fleet/placements` | GET `fleet.read` | The applications recorded as running on several servers, with their members |
| `/api/fleet/placements/{domain}` | GET `fleet.read`; PUT `fleet.manage`; DELETE `fleet.manage` | One placement and its servers compared (the commit each serves, which differs, who is missing or unreachable); record or change it (`{nodes, labels, serial, max_failures, canary, rollback_on_failure}`, touches no server); forget it |
| `/api/fleet/placements/{domain}/copy` | POST `fleet.manage` | Put the application on servers that lack it, from one that has it (`{from_node, to, new_domain, secrets, match_commit, plan}`): a fleet job; the document passes through the central's memory and is written nowhere |
| `/api/fleet/placements/{domain}/rollout` | POST `fleet.manage` | Roll one commit, tag or image tag out over the servers (`{commit, tag, image_tag, force, serial, max_failures, canary, rollback_on_failure, plan}`): the first server goes alone and its commit is fixed for the rest; a fleet job that puts the servers already moved back when it stops |
| `/api/fleet/placements/{domain}/upstreams` | GET `fleet.read` | The servers written for a load balancer (`format` `nginx`, `list` or `json`; `port`), marking those that serve now |
| `/api/fleet/servers` | GET `fleet.read` |  |
| `/api/fleet/servers/{node}/labels` | PUT `fleet.manage` |  |
| `/api/fleet/summary` | GET `fleet.read` |  |
| `/api/fleet/updates` | GET `fleet.read` |  |
| `/api/nodes` | GET `fleet.read`; POST `fleet.manage`, sudo, four-eyes |  |
| `/api/nodes/{node}` | GET `fleet.read`; DELETE `fleet.manage`, sudo, four-eyes |  |
| `/api/nodes/{node}/inventory` | GET `fleet.read`; POST `fleet.manage`, admin scope | What this central knows about a server, to take to another central (no secret in it); record the inventory another central exported for it (`{document, include_ssh}`), checked whole first |
| `/api/nodes/{node}/key` | GET `fleet.manage` | The central's key for a new server and the command to run on it |
| `/api/nodes/{node}/monitoring` | PUT `fleet.manage` | Pause a server's monitoring for maintenance, or resume it: a paused server is not asked, not counted as down and never announced |
| `/api/nodes/{node}/ssh` | GET `fleet.read`; PUT `fleet.manage`, sudo | How the central connects to a server: the wait for the connection (1 to 300 seconds) and a bastion with its pinned host key; setting it is tried first and put back when the server does not answer. A bastion's host key is given (`fingerprint` or `host_key`), never learnt on first use |
| `/api/nodes/{node}/ssh/authorization` | POST `fleet.manage` | The line the bastion's operator adds, asked for with the bastion that is about to be set (its address and key or fingerprint); changes nothing, so the bastion can let the central in before the change is tried |
| `/api/nodes/{node}/test` | POST `fleet.read` |  |

### Configuration, integrations and recipes

| Endpoint | Method, permission | |
|---|---|---|
| `/api/config` | GET `settings.read`; PUT `security.manage`, sudo; PATCH `settings.manage`, sudo |  |
| `/api/config/apps-directory` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/config/backup` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/config/defaults` | GET `settings.read` |  |
| `/api/config/delivery` | GET `settings.read`; PUT `settings.manage`, sudo | The limits this server delivers under: `max_concurrent_builds`, `queue_limit` and `max_minutes` (the longest a build may take; 0 is no limit). Read again by the queue within seconds |
| `/api/config/notifications/telegram` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/config/notifications/telegram/chats` | POST `settings.manage` |  |
| `/api/config/notifications/{channel}/test` | POST `apps.operate` |  |
| `/api/config/reload` | POST `settings.manage` |  |
| `/api/config/smtp` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/config/ssl` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/config/web` | GET `settings.read`; PUT `security.manage`, sudo |  |
| `/api/config/webserver` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/integrations/github` | GET `apps.read`; DELETE `apps.manage`, sudo |  |
| `/api/integrations/github/installations` | POST `apps.manage`, sudo |  |
| `/api/integrations/github/installations/sync` | POST `apps.manage` |  |
| `/api/integrations/github/manifest` | POST `apps.manage`, sudo |  |
| `/api/integrations/github/manifest/conversions` | POST `apps.manage`, sudo |  |
| `/api/integrations/github/repositories` | GET `apps.read` |  |
| `/api/integrations/github/repositories/{owner}/{repo}/branches` | GET `apps.read` |  |
| `/api/notifications/channels` | GET `settings.read` | Every channel (the default of each type, then the operator's own) without a credential, and the types a new one may be |
| `/api/notifications/channels/{name}` | PUT `settings.manage`, sudo; DELETE `settings.manage`, sudo | Create or change a channel: its settings, its own switch per event, its route (applications, projects, environments, tags, lowest severity) and its quiet hours. A `***` in a credential keeps the stored one. Deleting a default channel empties it |
| `/api/notifications/deliveries` | GET `settings.read` | The record of deliveries, newest first, and how many are in each state |
| `/api/notifications/deliveries/sweep` | POST `settings.manage` | Try what is due now |
| `/api/notifications/deliveries/{delivery_id}/retry` | POST `settings.manage` | Send a failed, waiting or held delivery again now |
| `/api/notifications/events` | GET `settings.read` | Every event kind, whether it is on by default and whether it is on now |
| `/api/recipes` | GET `apps.read` |  |
| `/api/recipes/{name}` | GET `apps.read` |  |

### Accounts, sessions and tokens

| Endpoint | Method, permission | |
|---|---|---|
| `/api/auth/2fa` | GET `self` |  |
| `/api/auth/2fa/backup-codes` | POST `self`, sudo |  |
| `/api/auth/2fa/confirm` | POST `self` |  |
| `/api/auth/2fa/disable` | POST `self`, sudo |  |
| `/api/auth/2fa/enroll` | POST `self` |  |
| `/api/auth/accounts` | GET `accounts.read`; POST `accounts.manage`, sudo, four-eyes |  |
| `/api/auth/accounts/{username}` | GET `accounts.read`; PATCH `accounts.manage`, sudo, four-eyes; DELETE `accounts.manage`, sudo |  |
| `/api/auth/accounts/{username}/disable` | POST `accounts.manage`, sudo |  |
| `/api/auth/accounts/{username}/enable` | POST `accounts.manage`, sudo |  |
| `/api/auth/accounts/{username}/reset-mfa` | POST `accounts.manage`, sudo |  |
| `/api/auth/accounts/{username}/unlock` | POST `accounts.manage`, sudo |  |
| `/api/auth/elevate` | POST `self` |  |
| `/api/auth/exceptions` | GET `accounts.read`; POST `accounts.manage`, sudo |  |
| `/api/auth/exceptions/{exception_id}` | DELETE `accounts.manage`, sudo |  |
| `/api/auth/fleet/revoke` | POST `self` |  |
| `/api/auth/fleet/self` | GET `self` | This server's ceiling for the calling central |
| `/api/auth/invitations` | POST `accounts.manage`, sudo, four-eyes |  |
| `/api/auth/invitations/accept` | POST `public` |  |
| `/api/auth/invitations/open` | POST `public` |  |
| `/api/auth/login` | POST `public` |  |
| `/api/auth/logout` | POST `self` |  |
| `/api/auth/notice/accept` | POST `self` |  |
| `/api/auth/oidc/callback` | GET `public` | The provider's redirect; answers a redirect to the sign-in page, counted by the lockout |
| `/api/auth/oidc/complete` | POST `public` | Collects the single-use ticket; counted by the lockout |
| `/api/auth/oidc/config` | GET `accounts.read` |  |
| `/api/auth/oidc/config/{provider}` | PUT `accounts.manage`, sudo; DELETE `accounts.manage`, sudo |  |
| `/api/auth/oidc/config/{provider}/test` | POST `accounts.manage` |  |
| `/api/auth/oidc/identities` | GET `accounts.read`; POST `accounts.manage`, sudo |  |
| `/api/auth/oidc/identities/{identity_id}` | DELETE `accounts.manage`, sudo |  |
| `/api/auth/oidc/providers` | GET `public` | Name and button label of each enabled provider |
| `/api/auth/oidc/start` | GET `public` | Redirects to the provider |
| `/api/auth/passkeys` | GET `self` |  |
| `/api/auth/passkeys/elevate` | POST `self` |  |
| `/api/auth/passkeys/elevate/options` | POST `self` |  |
| `/api/auth/passkeys/login` | POST `public` |  |
| `/api/auth/passkeys/login/options` | POST `public` |  |
| `/api/auth/passkeys/registration` | POST `self` |  |
| `/api/auth/passkeys/registration/options` | POST `self` |  |
| `/api/auth/passkeys/{passkey_id}` | PATCH `self`; DELETE `self` |  |
| `/api/auth/password` | POST `self` |  |
| `/api/auth/roles` | GET `self` |  |
| `/api/auth/session` | GET `public` |  |
| `/api/auth/sessions` | GET `self` |  |
| `/api/auth/sessions/revoke-all` | POST `self` |  |
| `/api/auth/sessions/revoke-others` | POST `self` |  |
| `/api/auth/sessions/{sid_prefix}` | DELETE `self` |  |
| `/api/auth/tokens` | GET `self`; POST `self`, sudo |  |
| `/api/auth/tokens/{token_id}` | DELETE `self` |  |
| `/api/auth/verify` | GET `self` |  |
| `/api/auth/ws-ticket` | POST `self` |  |

### Approvals, audit and compliance

| Endpoint | Method, permission | |
|---|---|---|
| `/api/approvals` | GET `self` |  |
| `/api/approvals/policy` | GET `self` |  |
| `/api/approvals/{approval_id}` | GET `self` |  |
| `/api/approvals/{approval_id}/approve` | POST `self`, sudo |  |
| `/api/approvals/{approval_id}/reject` | POST `self`, sudo |  |
| `/api/audit` | GET `audit.read` |  |
| `/api/audit/events` | GET `audit.read` |  |
| `/api/audit/export` | GET `audit.read` |  |
| `/api/audit/reviews` | GET `audit.read`; POST `audit.read` |  |
| `/api/audit/status` | GET `audit.read` |  |
| `/api/audit/verify` | GET `audit.read` | Check the chain |
| `/api/ens/access-review` | GET `accounts.read`; POST `accounts.manage` |  |
| `/api/ens/check` | GET `compliance.read` |  |
| `/api/ens/incident` | GET `compliance.read` |  |
| `/api/ens/inventory` | GET `apps.read` |  |
| `/api/ens/inventory/{domain}` | PUT `apps.manage` |  |
| `/api/ens/profile` | GET `settings.read` |  |
| `/api/ens/report` | GET `compliance.read` | The evidence bundle |

### Other routes

| Endpoint | Method, permission | |
|---|---|---|
| `/api/openapi.json` | GET `self` |  |
| `/health` | GET `public` |  |
| `/hooks/deploy/{domain}` | POST `public` |  |
| `/hooks/github` | POST `public` |  |


FastAPI's own `/docs`, `/redoc` and `/openapi.json` are disabled: the schema of an API that
runs systemd as root is only served to authenticated callers.
