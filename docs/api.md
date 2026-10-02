# API

Everything the console does, it does through the API described here: the console is a
client of it like any script. The contract is exported as OpenAPI and served, authenticated,
at `GET /api/openapi.json`.

Examples use `https://panel.example.com` for the console's address and `$TOKEN` for a
credential. On a console bound to loopback, run them on the server against
`http://127.0.0.1:8080`, or through an SSH tunnel (see [console.md](console.md)).

## Authentication

Two ways in, for two kinds of client: tokens for scripts, sessions for browsers. Whoever comes
in, every route checks one permission (see [Permissions](#permissions)).

### Bearer tokens, for scripts

```bash
noust token create ci-update --scope deploy --owner alice    # prints the token once
noust token create dashboard --scope read --expires-hours 720
curl -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/apps
```

API tokens start with `noust_tok_` (`wasm_tok_` for tokens issued before 3.0, which keep
working), are shown once, and are stored only as a salted hash. Manage them with
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
sends `X-WASM-CSRF` like a browser does. Passkeys sign in with
`POST /api/auth/passkeys/login/options` and then `POST /api/auth/passkeys/login`. The answer is
`{success, expires_in, csrf_token, session_token}`, and two cookies are set:

- `wasm_session`: `HttpOnly`, `SameSite=Strict`, `Secure` over TLS.
- `wasm_csrf`: readable by the page, holding the CSRF token.

Every `POST`, `PUT`, `PATCH` and `DELETE` made with a session, in the cookie or as a Bearer
token, must echo the CSRF token in the `X-WASM-CSRF` header. `GET /api/auth/session` (no
credential required) reports whether the caller is signed in, its account and role, its
permissions, `expires_at`, `elevated_until`, whether a second factor is set up, and the CSRF
header and cookie names. Before sign-in it does not reveal the host name, the version or
whether 2FA is on. A credential presented to it that is wrong counts toward the lockout like
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

### Permissions

Every route needs one permission, published in `/api/openapi.json` as the operation's
`x-noust-permission`: `public` (no credential), `self` (any signed-in principal, acting on its
own session, tokens, second factor and passkeys), or one of `apps.read`, `apps.operate`,
`apps.deploy`, `apps.manage`, `secrets.reveal`, `root_equivalent`, `server.read`,
`server.manage`, `server.host_access`, `databases.read`, `databases.write`, `databases.manage`,
`backups.read`, `backups.run`, `backups.manage`, `fleet.read`, `fleet.manage`, `settings.read`,
`settings.manage`, `security.manage`, `accounts.read`, `accounts.manage`, `audit.read`,
`audit.manage`, `compliance.read`. `GET /api/auth/roles` lists what each role holds; the table
is in [security.md](security.md#roles-and-permissions).

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
  Only a job that has not started can be cancelled (`POST /api/jobs/{id}/cancel`).
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
| `/ws/events` | Journal entries of Noust's own units: `noust-*` (cron and backup timers, the monitor), and their `wasm-*` names on a server not yet migrated from WASM. |

A handshake authenticates with any one of:

- the session cookie (a browser on the console's own origin);
- `Authorization: Bearer <token>` (any client that can set headers);
- the subprotocol header `Sec-WebSocket-Protocol: wasm.auth, wasm.token.<token>`;
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
| GitLab | `X-Gitlab-Token: <secret>` |

A push to a branch other than the one the application deploys (its pinned branch; without a
pin, the branch its checkout is on in place or its recorded branch on releases) answers
`200 {"status": "ignored", "reason": "branch"}`; with no branch at all to go by, any push
deploys. A forge's ping is answered and recorded; a forge retry of the same delivery answers `"reason": "duplicate"`.
An application that **follows tags** (`noust app follow-tags DOMAIN 'v*'`, `PATCH
/api/apps/{domain}/follow-tags`) reads its deliveries differently (3.2): a published `release` of
GitHub or Gitea (not a draft or a pre-release) and a pushed tag (GitHub, Gitea or GitLab)
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
| `/api/apps/{domain}/export` | GET `secrets.reveal` |  |
| `/api/apps/{domain}/follow-tags` | PATCH `apps.manage`, sudo | `{pattern}`: deploy the newest git tag that matches a glob (`v*`) instead of a branch; null follows a branch again |
| `/api/apps/{domain}/headless` | GET `apps.read`; POST `apps.manage`, sudo | Whether a Compose stack publishes no port; record it as a worker (`{remove_site}` also retires the site Noust wrote) |
| `/api/apps/{domain}/health` | PATCH `apps.manage`, sudo | `{path, expect, timeout}` of the health gate; null is the default |
| `/api/apps/{domain}/hooks` | GET `apps.read`; PUT `root_equivalent`, sudo, four-eyes; DELETE `apps.manage` | The hooks the next deployment runs and where they come from; set the operator's own (`{document}`, a YAML `hooks:`), which replace the repository's `noust.yaml`; clear them |
| `/api/apps/{domain}/identity` | GET `apps.read` | The system account it runs as, whether it is its own, and why not when it cannot be |
| `/api/apps/{domain}/identity/migrate` | POST `root_equivalent`, sudo, four-eyes | Move it to its own account behind the health gate (job) |
| `/api/apps/{domain}/limits` | PATCH `apps.manage`, sudo | `{memory_max_mb, cpu_quota_percent, tasks_max, restart}`; null removes a limit |
| `/api/apps/{domain}/logs` | GET `apps.read` |  |
| `/api/apps/{domain}/metrics` | GET `apps.read` |  |
| `/api/apps/{domain}/migrate` | POST `apps.manage`, sudo |  |
| `/api/apps/{domain}/migrate/plan` | GET `apps.read` |  |
| `/api/apps/{domain}/previews` | GET `apps.read` |  |
| `/api/apps/{domain}/previews/settings` | PUT `apps.manage`, sudo; DELETE `apps.manage`, sudo |  |
| `/api/apps/{domain}/previews/{number}` | DELETE `apps.manage`, sudo |  |
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
| `/api/apps/{domain}/start` | POST `apps.operate` |  |
| `/api/apps/{domain}/stop` | POST `apps.operate` |  |
| `/api/apps/{domain}/webhook` | GET `apps.read` | How far the deploy webhook is set up |
| `/api/apps/{domain}/webhook-secret` | POST `apps.manage`, sudo; DELETE `apps.manage`, sudo | Create or rotate the secret (shown once); disable the webhook |
| `/api/apps/{domain}/webhook/deliveries` | GET `apps.read` | Deployments webhooks triggered |
| `/api/apps/{domain}/webhook/received` | GET `apps.read` | Every delivery received, ignored and refused ones included |
| `/api/apps/{domain}/webhook/reveal` | POST `secrets.reveal`, sudo |  |
| `/api/apps/{domain}/zero-downtime` | GET `apps.read`; PUT `apps.manage`, sudo | `{enabled, drain_seconds}`; a Docker Compose stack is accepted since 3.2, updated behind relays |

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
| `/api/jobs/delete` | POST `apps.manage`, sudo | `{domain, remove_files, remove_ssl, remove_adopted_directory}` |
| `/api/jobs/rollback` | POST `apps.deploy` | `{domain, backup_id, schema_changed_ok}`: restore a backup. `409 schema_changed` unless confirmed |
| `/api/jobs/update` | POST `apps.deploy` | `{domain, force}` |
| `/api/jobs/{job_id}` | GET `apps.read` |  |
| `/api/jobs/{job_id}/cancel` | POST `apps.operate` |  |
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
| `/api/fleet/jobs` | GET `fleet.read` |  |
| `/api/fleet/jobs/{job_id}` | GET `fleet.read` |  |
| `/api/fleet/jobs/{job_id}/retry` | POST `fleet.manage` |  |
| `/api/fleet/servers` | GET `fleet.read` |  |
| `/api/fleet/servers/{node}/labels` | PUT `fleet.manage` |  |
| `/api/fleet/summary` | GET `fleet.read` |  |
| `/api/fleet/updates` | GET `fleet.read` |  |
| `/api/nodes` | GET `fleet.read`; POST `fleet.manage`, sudo, four-eyes |  |
| `/api/nodes/{node}` | GET `fleet.read`; DELETE `fleet.manage`, sudo, four-eyes |  |
| `/api/nodes/{node}/key` | GET `fleet.manage` | The central's key for a new server and the command to run on it |
| `/api/nodes/{node}/test` | POST `fleet.read` |  |

### Configuration, integrations and recipes

| Endpoint | Method, permission | |
|---|---|---|
| `/api/config` | GET `settings.read`; PUT `security.manage`, sudo; PATCH `settings.manage`, sudo |  |
| `/api/config/apps-directory` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/config/backup` | GET `settings.read`; PUT `settings.manage`, sudo |  |
| `/api/config/defaults` | GET `settings.read` |  |
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
