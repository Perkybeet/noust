# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The numbers and names the console's sign-in is built on: lifetimes, limits, the cookie,
header and subprotocol names, the scopes and the paths each one reaches.
"""

from __future__ import annotations

import re

from noust.core import legacy_names

#: Bytes of entropy in the master token and in the signing key.
TOKEN_LENGTH = 32
SECRET_KEY_LENGTH = 64

#: Five wrong tokens is far more than a human typing a copy-pasted secret needs,
#: and the 15 minute lockout turns an online guessing attack against a 256-bit
#: token into something with no practical end.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = 900

#: The budget of a request that carries no valid credential, per client IP:
#: the sign-in page, the forge webhooks, a scanner. Nothing anonymous needs 2/s
#: sustained, and it is what an attacker without a credential gets.
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX_REQUESTS = 120

#: The budget of a request that carries a valid credential, per credential
#: (one sign-in, one API token, the master token) rather than per address. The
#: console alone exceeds 120 a minute following a deploy and navigating, and
#: over an SSH tunnel every request comes from 127.0.0.1, so a per-address
#: budget made every tab and script share one. Holding a valid credential is
#: what guessing one would win, so this protects the machine from a runaway
#: client, not from an attacker; credential guessing is the lockout's job.
RATE_LIMIT_AUTHENTICATED_MAX_REQUESTS = 1200

#: Upper bound on tracked client IPs, so a spoofed-source flood cannot turn the
#: rate limiter into unbounded memory growth.
RATE_LIMIT_MAX_TRACKED_IPS = 4096

#: Where the signing key, the master token hash, the session database and the
#: audit log live: the configuration directory, ``/etc/noust`` (see
#: :func:`noust.core.paths.config_dir`). Overridable for tests and for unprivileged
#: installs with this variable.
STATE_DIR_ENV = "NOUST_WEB_STATE_DIR"

SECRET_FILE_NAME = "web-secret"  # noqa: S105 - file name, not a credential
TOKEN_FILE_NAME = "web-token"  # noqa: S105 - file name, not a credential
TOTP_FILE_NAME = "web-totp"
SESSION_DB_NAME = "web-sessions.db"
AUDIT_LOG_NAME = "web-audit.log"

#: Single-use recovery codes issued when TOTP is confirmed. Eight is what an
#: operator can print on one line; each is 32 bits, which a five-attempt
#: lockout makes unguessable in practice and single use makes worthless after.
BACKUP_CODE_COUNT = 8

SESSION_COOKIE_NAME = "noust_session"
CSRF_COOKIE_NAME = "noust_csrf"
CSRF_HEADER_NAME = "X-Noust-CSRF"
#: The header a script written before 4.0 sends. Read beside the new one: the value it
#: carries is the same, only the name differs (see :mod:`noust.core.legacy_names`).
CSRF_HEADER_NAMES = (CSRF_HEADER_NAME, legacy_names.CSRF_HEADER)
#: The cookies of a console from before 4.0. They are not read, so a browser signs in once;
#: the sign-in and the sign-out expire them.
LEGACY_COOKIE_NAMES = (legacy_names.SESSION_COOKIE, legacy_names.CSRF_COOKIE)

#: Methods that do not change state and therefore do not need a CSRF token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: WebSocket tickets are single use and only have to survive the handshake.
WS_TICKET_TTL = 30

#: How long the second step of an account's sign-in waits for its code or
#: passkey once the password step passed: long enough to find a phone.
LOGIN_CHALLENGE_TTL = 300
#: Wrong codes one second step takes before it is spent and the sign-in has
#: to start again from the password. The account's own lockout counts them too.
LOGIN_CHALLENGE_ATTEMPTS = 5

#: What a sign-in from an address many people share is counted under, with the
#: address and the name it named (:func:`sign_in_lockout_key`).
SIGN_IN_LOCKOUT_PREFIX = "sign-in:"

#: Close codes for a handshake the middleware refuses. They are in the private
#: 4000-4999 range so a client can tell "log in again" from "you are blocked".
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_RATE_LIMITED = 4429

#: Wrong webhook signatures one application's hook tolerates inside
#: :data:`WEBHOOK_LOCKOUT_DURATION` before it refuses deliveries for that long.
#: Counted per application, never per address: a forge delivers every
#: customer's webhooks from a few shared addresses, so an address lockout fed
#: by signatures let anyone with an account on the forge cut off everyone.
#: Ten is more wrong deliveries than a misconfigured secret produces between
#: the operator noticing and fixing it; the secret itself is 256 bits.
WEBHOOK_MAX_FAILURES = 10
WEBHOOK_LOCKOUT_DURATION = 900

#: Largest request body the panel reads, checked before authentication. The
#: API's biggest legitimate bodies - a unit file, a site configuration, an
#: ``.env`` - are a few kilobytes; a mebibyte is generous and still bounded.
MAX_BODY_BYTES = 1024 * 1024

#: The forge webhook's own cap. GitHub sends push payloads of up to 25 MB, but
#: those are pushes of hundreds of commits; the hook reads nothing but the
#: branch, and an ordinary push is a few kilobytes. A delivery over this is
#: refused with 413, which the forge shows in its delivery log.
MAX_HOOK_BODY_BYTES = 5 * 1024 * 1024

#: WebSockets one credential may hold open at once. Every log socket is a
#: ``journalctl -f`` running as root, so without a cap one token could hold
#: the process table hostage. A console tab uses one or two (the log drawer,
#: a job it follows); eight leaves room for several tabs and a script.
WS_MAX_PER_CREDENTIAL = 8

#: Event streams (``GET /events``) one credential may hold open at once. Each is
#: a coroutine, a queue, a subscription and, every few seconds, a render of the
#: machine in a worker thread, for as long as the client stays: without a cap
#: one token could open streams until the console that runs as root had nothing
#: left for anyone else. A console tab holds one; sixteen leaves room for
#: several tabs, a wall display and a script.
EVENT_STREAMS_MAX_PER_CREDENTIAL = 16

#: Subprotocol prefix carrying a session token, for clients that cannot send a
#: cookie: ``Sec-WebSocket-Protocol: noust.auth, noust.token.<token>``.
WS_SUBPROTOCOL = "noust.auth"
WS_TOKEN_PREFIX = "noust.token."  # noqa: S105 - subprotocol prefix, not a credential
#: Both spellings, the current one first: a client that offers only the old one is
#: answered with the old one, which is what a browser's handshake requires.
WS_SUBPROTOCOLS = (WS_SUBPROTOCOL, legacy_names.WS_SUBPROTOCOL)
WS_TOKEN_PREFIXES = (WS_TOKEN_PREFIX, legacy_names.WS_TOKEN_PREFIX)

#: Prefix that routes a Bearer credential to the API token table instead of
#: the session store or the master token. The master token's own prefix is
#: ``noust_``, so the two are distinguishable at a glance in a config file.
API_TOKEN_PREFIX = "noust_tok_"  # noqa: S105 - a prefix, not a credential
#: What API tokens issued before 3.0 start with. They are stored hashed and
#: keep working until revoked or expired; only new tokens get the new prefix.
LEGACY_API_TOKEN_PREFIX = legacy_names.API_TOKEN_PREFIX
API_TOKEN_PREFIXES = (API_TOKEN_PREFIX, LEGACY_API_TOKEN_PREFIX)

#: The ``sid`` of the master token's payload, and of a WebSocket ticket it was
#: issued to. API tokens use ``token:<name>``; cookie sessions a random hex id.
MASTER_SID = "master"
API_TOKEN_SID_PREFIX = "token:"  # noqa: S105 - a prefix, not a credential

#: API token scopes, weakest first. The order is the hierarchy: a scope
#: satisfies every requirement at or below its own rank.
API_TOKEN_SCOPES = ("read", "deploy", "admin")
SCOPE_RANK = {scope: rank for rank, scope in enumerate(API_TOKEN_SCOPES)}

#: How often a token's last_used_at is written, at most. Recording every
#: request would turn a polling dashboard into a constant stream of writes to
#: a database whose value here is "when was this credential last alive".
API_TOKEN_LAST_USED_THROTTLE = 60

#: The mutations a ``deploy`` scope is for: moving an application that already
#: exists - an update, a rollback. Every other mutation - creating an
#: application, inspecting a source, deleting, editing configuration, managing
#: credentials - stays ``admin``. Creating and inspecting are not deploy
#: operations in this sense: both fetch a source the caller names and build or
#: read it as root, so a token handed to CI would otherwise be able to point
#: the machine at any repository, or any directory on it. This is the whole
#: scope policy, stated once and enforced at the same chokepoint that resolves
#: the credential; see :func:`required_scope`.
DEPLOY_SCOPE_PATHS = frozenset(
    {
        "/api/jobs/update",
        "/api/jobs/rollback",
        # What CI calls once it built and pushed (noust.web.api.app_deploy).
        "/api/deploy",
    }
)

#: The same policy for mutations whose path carries a parameter, which no
#: exact path can name. Each pattern is anchored at both ends and a segment
#: never matches a ``/``, so a pattern cannot be satisfied by a longer path
#: that merely contains it.
DEPLOY_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Activating a release is an instant rollback: the same act as queueing one.
    re.compile(r"^/api/apps/[^/]+/releases/[^/]+/activate$"),
    # Rebuilding a deployment's commit is an update; going back to what a
    # deployment produced is a rollback.
    re.compile(r"^/api/apps/[^/]+/deployments/[0-9]+/(rebuild|rollback)$"),
    # The per-application deploy URL of a pipeline.
    re.compile(r"^/api/apps/[^/]+/deploy$"),
)

#: A node-proxied path's prefix (``/api/nodes/{node}/api/...``, from
#: ``noust.web.api.node_proxy``), before the node's own path takes over.
#: Stripped before the table above is matched, so a request through a node
#: is scored the same way it would be against that node's own API: the node
#: still enforces its own scope, narrowed from ``X-Noust-Actor-Scope``, so
#: nothing here needs to trust what the central decided.
_NODE_PROXY_PATH = re.compile(r"^/api/nodes/[^/]+(/api/.*)$")

# ---------------------------------------------------------------- the fleet
#
# A central reaches this server through an SSH tunnel that ends on
# 127.0.0.1, carrying the token ``noust fleet authorize`` minted for it. That
# token is the only credential allowed to speak for somebody else, so every
# rule about it is stated here, next to the credential checks it modifies,
# and enforced where a credential becomes a payload.

#: Scope of the token a central holds for this server. It acts with ``admin``
#: authority, narrowed to the scope of the operator it acts for, minus
#: :func:`fleet_refusal`'s set, and only from :func:`fleet_origin_refusal`'s
#: loopback.
FLEET_SCOPE = "fleet"

#: Who, on the central, is behind a fleet request. Honoured only on a fleet
#: token; on any other credential it is ignored, so it cannot relabel a
#: session's own audit trail.
FLEET_ACTOR_HEADER = "X-Noust-Actor"
#: The scope that operator holds on the central. The fleet token is narrowed
#: to it, so a read-only token on the central reads a node exactly as a
#: read-only token on the node would: admin-only GETs and process command
#: lines included. What a 3.0 central sends; a 3.1 one sends the role too.
FLEET_ACTOR_SCOPE_HEADER = "X-Noust-Actor-Scope"
#: The role that operator's account holds on the central. When present the
#: node grants that role's permissions from its own table, narrowed to its
#: ceiling (:func:`noust.fleet.policy.permits`), and ignores the scope; an
#: unknown role reads, never more.
FLEET_ACTOR_ROLE_HEADER = "X-Noust-Actor-Role"
FLEET_ROLE_PATTERN = re.compile(r"[a-z][a-z_-]{0,31}")
#: ``1`` when that operator's own credential on the central is inside sudo
#: mode, or is one sudo mode does not ask (the master token, an API token).
#: The node's sudo window cannot be opened by a token, so this is how an
#: elevated action - including the ones only a request body makes elevated,
#: such as a write query - is confirmed by the one party that saw the operator.
FLEET_ELEVATED_HEADER = "X-Noust-Elevated"

#: A central's actor label: ``master``, ``token:<name>`` or a session id
#: prefix. Anything that could forge a second audit field, or a line, is out.
FLEET_ACTOR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,63}")

#: Headers a reverse proxy adds. The tunnel adds none; a request that carries
#: one came through something listening on this machine for the outside world,
#: and a fleet token is not accepted through it even though its TCP peer is
#: loopback.
FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip")

#: The only authentication endpoints a fleet token may reach: the ones that
#: say who it is, and the one that revokes itself when the central removes the
#: node. Minting and revoking tokens, two-factor authentication,
#: sessions, sudo mode and WebSocket tickets are the node's own operator's.
FLEET_AUTH_PATHS = frozenset(
    {"/api/auth/session", "/api/auth/verify", "/api/auth/fleet/revoke", "/api/auth/fleet/self"}
)

#: Path prefixes a fleet token never reaches, whatever the method: the node's
#: own credentials, and fleet enrollment - a central must not be able to use a
#: node as a hop to the node's own nodes, nor enroll it anywhere else.
FLEET_REFUSED_PREFIXES = ("/api/auth", "/api/nodes", "/api/fleet", "/api/central", "/ws/nodes")

#: Exact writes a fleet token is refused: replacing the whole configuration
#: (which carries the ``web`` block) and the console's own bind settings.
FLEET_REFUSED_WRITES = frozenset({("PUT", "/api/config"), ("PUT", "/api/config/web")})

#: Configuration sections that are this server's security settings: who may
#: reach its console and from where, its sign-in policy and audit trail, and
#: its fleet role. ``PATCH /api/config`` addresses one key by a dotted path in
#: its body; one under these is refused.
FLEET_PROTECTED_CONFIG_SECTIONS = frozenset(
    {"web", "fleet", "central", "auth", "security", "audit", "approval"}
)

#: Recorded in the payload the auth dependency hands to endpoints. Kept as
#: names rather than a JWT: see SessionStore._encode for why the JWT went.
SESSION_ISSUER = "noust-web"
SESSION_SUBJECT = "noust_session"
JWT_EXPIRATION_HOURS = 12

#: A session is re-issued once it is past this fraction of its lifetime, so an
#: active operator is never logged out mid-deploy while idle sessions still die.
SESSION_RENEW_RATIO = 0.5

#: How long a rotated session id keeps working after it is replaced. Long
#: enough for the requests a dashboard already had in flight, short enough that
#: a captured cookie is worthless by the time it is replayed.
SESSION_ROTATION_GRACE = 30

#: How many rotations :meth:`TokenManager._lineage` follows from a retired id. A session rotates
#: at most every half of its lifetime, so a chain this long is a loop in the store, not a login.
MAX_ROTATION_HOPS = 8

#: Hard ceiling on a session's life, however active it is. Renewal resets the
#: idle clock but never this one, so a stolen cookie that is kept warm still
#: stops working within a day. The sign-in policy
#: (:attr:`noust.core.accounts.AuthPolicy.absolute_hours`, 12 hours, 8 under
#: the ENS profile) is the ceiling that normally applies; this one caps it.
SESSION_MAX_HOURS = 24

#: How often a session's last activity is written, at most. The idle timeout
#: is minutes long; a write per request would be a write per poll.
SESSION_ACTIVITY_THROTTLE = 30

#: What a session is: a person's account, or the master token signed in.
SESSION_KIND_ACCOUNT = "account"
SESSION_KIND_MASTER = "master"

#: The audit log is written by anonymous, unauthenticated events (a refused
#: handshake is one), so it is rotated rather than allowed to fill the disk.
AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUDIT_BACKUPS = 3

FILE_MODE = 0o600
DIR_MODE = 0o700

#: Sudo mode (D5) is a window, not a fixed ten minutes: ``POST /api/auth/elevate``
#: opens it for the sign-in policy's idle time (``auth.sudo.idle_minutes``), every
#: elevated action performed in it pushes the end out by that much again, and it
#: never outlives ``auth.sudo.max_minutes`` from the confirmation or the session
#: itself. Destructive actions - deleting an app, a database, a service or a
#: site, revealing a .env, writing raw config - all require it.
#:
#: The end is pushed out only when that gains more than this many seconds, so a
#: burst of elevated requests is one write to the session store, like
#: :data:`SESSION_ACTIVITY_THROTTLE` does for the idle clock.
ELEVATION_SLIDE_THRESHOLD = 60
