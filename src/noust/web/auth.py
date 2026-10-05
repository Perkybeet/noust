"""
Authentication and session security for the Noust web panel.

The panel drives systemd, nginx and certbot as root, so a session here is
equivalent to a root shell. The design decisions that follow from that:

- **The browser never holds the session in JavaScript.** Sessions travel in a
  ``HttpOnly``/``SameSite=Strict`` cookie (``Secure`` whenever the request is
  served over TLS), so an XSS bug cannot read the credential. ``Authorization:
  Bearer`` stays supported for the CLI and automation, which have no cookie
  jar and no ambient-authority problem.
- **Cookie authentication requires a CSRF token on every mutation.** The
  scheme is double-submit *bound to the session*: the CSRF value is generated
  with the session, stored server-side, mirrored in a readable cookie, and has
  to come back in the ``X-WASM-CSRF`` header. A cross-site attacker can neither
  read the cookie nor set a custom header without a CORS preflight the server
  refuses, and unlike plain double-submit a cookie injected by a sibling
  subdomain does not match the stored value.
- **Client identity comes from the TCP peer.** ``X-Forwarded-For`` is honoured
  only when the peer is a configured trusted proxy, and only when the value it
  carries parses as an IP address. Otherwise the IP whitelist, the rate limiter
  and the brute-force lockout could all be defeated by rotating a header, or by
  turning the limiter's key into a string the attacker picks.
- **Every rejected credential is counted in one place.** Cookies, ``Bearer``
  headers and WebSocket handshakes all fail through :func:`record_auth_failure`,
  so a lockout cannot be escaped by changing channel or endpoint.
- **Secrets and sessions are persisted, never invented on the fly.** A signing
  key that silently regenerates logs everyone out on restart and makes multiple
  workers impossible; if the key cannot be written, or exists but is empty, the
  server refuses to start instead of quietly issuing a new one.
- **Sessions die of old age.** Renewal keeps an active operator logged in, but
  it rotates the session id and never pushes the absolute deadline, so a session
  that is used continuously still expires.
- **Every request acts as someone, with permissions.** A session is a person's
  account (read from the store on every request, so disabling it or changing
  its role applies at once) or the master token, which holds the console only
  while no account exists and is break-glass after. :func:`require_auth` holds
  the principal to the permission its route declares in
  :mod:`noust.web.permissions`.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import importlib
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from starlette.requests import HTTPConnection

from noust.core import paths, totp
from noust.core.accounts import AccountManager, AuthPolicy, load_policy
from noust.core.accounts.model import Account
from noust.core.audit import install as install_core_audit_log
from noust.core.audit.legacy import AuditLogger
from noust.core.exceptions import SecurityError
from noust.core.fs import SECRET_MODE, get_fs, is_rehearsal
from noust.core.store import StoreError
from noust.web.permissions import ALL_PERMISSIONS, PUBLIC, Permission
from noust.web.permissions.enforce import (
    PermissionDenied,
    check_permission,
    has_permission,
    patched_config_key,
    required_permissions,
)
from noust.web.permissions.roles import (
    GRANT_BREAK_GLASS,
    GRANT_COMPAT,
    GRANT_PERMISSIONS,
    GRANT_RECOVERY,
    LEGACY_SCOPES,
    ROLE_PERMISSIONS,
    TOKEN_SCOPES,
    legacy_scope,
    permissions_for_role,
    permissions_for_scope,
)

logger = logging.getLogger(__name__)

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
#: audit log live: the configuration directory, ``/etc/noust`` (``/etc/wasm``
#: on a server not migrated yet, see :func:`noust.core.paths.config_dir`).
#: Overridable for tests and for unprivileged installs with this variable, or
#: with its WASM spelling, ``WASM_WEB_STATE_DIR``.
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

SESSION_COOKIE_NAME = "wasm_session"
CSRF_COOKIE_NAME = "wasm_csrf"
CSRF_HEADER_NAME = "X-WASM-CSRF"

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

#: Subprotocol prefix carrying a session token, for clients that cannot send a
#: cookie: ``Sec-WebSocket-Protocol: wasm.auth, wasm.token.<token>``.
WS_SUBPROTOCOL = "wasm.auth"
WS_TOKEN_PREFIX = "wasm.token."  # noqa: S105 - subprotocol prefix, not a credential

#: Prefix that routes a Bearer credential to the API token table instead of
#: the session store or the master token. The master token's own prefix is
#: ``noust_``, so the two are distinguishable at a glance in a config file.
API_TOKEN_PREFIX = "noust_tok_"  # noqa: S105 - a prefix, not a credential
#: What API tokens issued before 3.0 start with. They are stored hashed and
#: keep working until revoked or expired; only new tokens get the new prefix.
LEGACY_API_TOKEN_PREFIX = "wasm_tok_"  # noqa: S105 - a prefix, not a credential
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
SESSION_SUBJECT = "wasm_session"
JWT_EXPIRATION_HOURS = 12

#: A session is re-issued once it is past this fraction of its lifetime, so an
#: active operator is never logged out mid-deploy while idle sessions still die.
SESSION_RENEW_RATIO = 0.5

#: How long a rotated session id keeps working after it is replaced. Long
#: enough for the requests a dashboard already had in flight, short enough that
#: a captured cookie is worthless by the time it is replayed.
SESSION_ROTATION_GRACE = 30

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

#: How long POST /api/auth/elevate confirms a cookie session for, per D5:
#: "marca la sesion como elevada 10 minutos". Destructive actions - deleting
#: an app, a database, a service or a site, revealing a .env, writing raw
#: config - all require it.
ELEVATION_SECONDS = 600

#: Indirection over time.time(), so a test can advance a fake clock to expire
#: an elevation window without perturbing session expiry, which is computed
#: from the real wall clock elsewhere in this module.
_now: Callable[[], float] = time.time


def utcnow() -> datetime:
    """
    Return the current time as an aware UTC datetime.

    Returns:
        The current UTC time.
    """
    return datetime.now(timezone.utc)


@dataclass
class SecurityConfig:
    """
    Security configuration for the web interface.

    Attributes:
        host: Interface the server binds to.
        port: TCP port the server binds to.
        allowed_hosts: Host header values accepted by the deployment.
        enable_cors: Whether cross-origin requests are allowed at all.
        cors_origins: Explicit origins allowed when CORS is enabled.
        rate_limit_enabled: Whether rate limiting is applied.
        rate_limit_requests: Requests without a valid credential allowed per
            window and per client IP.
        rate_limit_authenticated_requests: Requests with a valid credential
            allowed per window and per credential.
        rate_limit_window: Length of the rate limit window in seconds.
        max_failed_attempts: Failed logins before an IP is locked out.
        lockout_duration: Lockout length in seconds.
        token_expiration_hours: Session lifetime in hours, refreshed by activity.
        session_max_hours: Absolute lifetime, never extended by activity.
        require_https: Refuse to serve or start without TLS when true.
        ssl_certfile: Path to the TLS certificate chain.
        ssl_keyfile: Path to the TLS private key.
        ip_whitelist: Client IPs or CIDRs allowed to reach the panel.
        trusted_proxies: Peer addresses whose forwarding headers are believed.
            Empty by default: an unconfigured deployment trusts nobody.
        bind_session_to_ip: Reject a session presented from a different IP.
        state_dir: Directory holding secrets, sessions and the audit log.
        audit_enabled: Whether privileged actions are written to the audit log.
        audit_max_bytes: Size at which the audit log is rotated.
        audit_backups: Rotated audit files kept before the oldest is deleted.
        ws_max_per_credential: WebSockets one credential may hold open at once.
        max_body_bytes: Largest request body read, before authentication.
        max_hook_body_bytes: The same for forge deliveries under ``/hooks/``.
        webhook_max_failures: Wrong signatures one application's hook takes
            before it refuses deliveries for ``webhook_lockout_duration``.
        webhook_lockout_duration: Seconds that window and that refusal last.
        auth_policy: The sign-in policy, fixed; None reads it from the
            configuration on every use (``security.profile`` and ``auth.*``),
            which is what a running console does.
    """

    host: str = "127.0.0.1"
    port: int = 8080
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])
    enable_cors: bool = False
    cors_origins: list[str] = field(default_factory=list)
    rate_limit_enabled: bool = True
    rate_limit_requests: int = RATE_LIMIT_MAX_REQUESTS
    rate_limit_authenticated_requests: int = RATE_LIMIT_AUTHENTICATED_MAX_REQUESTS
    rate_limit_window: int = RATE_LIMIT_WINDOW
    max_failed_attempts: int = MAX_FAILED_ATTEMPTS
    lockout_duration: int = LOCKOUT_DURATION
    token_expiration_hours: int = JWT_EXPIRATION_HOURS
    session_max_hours: int = SESSION_MAX_HOURS
    require_https: bool = False
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    ip_whitelist: list[str] = field(default_factory=list)
    trusted_proxies: list[str] = field(default_factory=list)
    bind_session_to_ip: bool = True
    state_dir: Path | None = None
    audit_enabled: bool = True
    audit_max_bytes: int = AUDIT_MAX_BYTES
    audit_backups: int = AUDIT_BACKUPS
    ws_max_per_credential: int = WS_MAX_PER_CREDENTIAL
    max_body_bytes: int = MAX_BODY_BYTES
    max_hook_body_bytes: int = MAX_HOOK_BODY_BYTES
    webhook_max_failures: int = WEBHOOK_MAX_FAILURES
    webhook_lockout_duration: int = WEBHOOK_LOCKOUT_DURATION
    auth_policy: AuthPolicy | None = None

    def policy(self) -> AuthPolicy:
        """
        Returns:
            The sign-in policy in force.
        """
        return self.auth_policy or load_policy()

    @property
    def resolved_state_dir(self) -> Path:
        """
        Directory used for secrets, sessions and audit records.

        Returns:
            The explicit ``state_dir``, else ``NOUST_WEB_STATE_DIR`` (or
            ``WASM_WEB_STATE_DIR``), else the configuration directory.
        """
        if self.state_dir is not None:
            return Path(self.state_dir)
        env_dir = paths.getenv("WEB_STATE_DIR")
        if env_dir:
            return Path(env_dir)
        return paths.config_dir()

    @property
    def secret_file(self) -> Path:
        """Path of the signing key file."""
        return self.resolved_state_dir / SECRET_FILE_NAME

    @property
    def token_file(self) -> Path:
        """Path of the master token hash file."""
        return self.resolved_state_dir / TOKEN_FILE_NAME

    @property
    def totp_file(self) -> Path:
        """Path of the two-factor state file."""
        return self.resolved_state_dir / TOTP_FILE_NAME

    @property
    def session_db(self) -> Path:
        """Path of the session database."""
        return self.resolved_state_dir / SESSION_DB_NAME

    @property
    def audit_log(self) -> Path:
        """Path of the audit log."""
        return self.resolved_state_dir / AUDIT_LOG_NAME


# Module-wide configuration, installed by the server factory.
_global_config: SecurityConfig | None = None
_global_token_manager: TokenManager | None = None
_global_audit_logger: AuditLogger | None = None
_global_brute_force: BruteForceProtection | None = None


def set_security_config(config: SecurityConfig) -> None:
    """
    Install the configuration used by helpers that only receive a request.

    Args:
        config: The active security configuration.
    """
    global _global_config
    _global_config = config


def get_security_config() -> SecurityConfig:
    """
    Return the active security configuration.

    Returns:
        The installed configuration, or a default one when the server has not
        been created yet.
    """
    return _global_config or SecurityConfig()


def set_token_manager(manager: TokenManager | None) -> None:
    """
    Install the token manager used by the authentication dependency.

    Args:
        manager: The manager to install, or None to clear it.
    """
    global _global_token_manager
    _global_token_manager = manager


def get_global_token_manager() -> TokenManager | None:
    """
    Return the installed token manager.

    Returns:
        The manager, or None when the server has not been created yet.
    """
    return _global_token_manager


def set_audit_logger(audit: AuditLogger | None) -> None:
    """
    Install the audit logger used by the API and the middleware.

    Args:
        audit: The logger to install, or None to disable auditing.
    """
    global _global_audit_logger
    _global_audit_logger = audit
    # One trail per process: what noust.core.audit.record writes (the CLI
    # hook, the host action ledger, new call sites) goes to the same log.
    install_core_audit_log(audit.log if audit is not None else None)


def get_audit_logger() -> AuditLogger | None:
    """
    Return the installed audit logger.

    Returns:
        The logger, or None when auditing is disabled.
    """
    return _global_audit_logger


def set_brute_force_protection(protection: BruteForceProtection | None) -> None:
    """
    Install the lockout tracker shared by every credential channel.

    Args:
        protection: The tracker to install, or None to clear it.
    """
    global _global_brute_force
    _global_brute_force = protection


def get_brute_force_protection() -> BruteForceProtection | None:
    """
    Return the installed lockout tracker.

    Returns:
        The tracker, or None when the server has not been created yet.
    """
    return _global_brute_force


def ensure_state_dir(path: Path) -> None:
    """
    Create the state directory with owner-only permissions.

    Through the filesystem seam, like every other change Noust makes, so a
    ``--dry-run`` reports the directory it would create and creates nothing.

    Args:
        path: Directory that must exist and be private.

    Raises:
        SecurityError: When the directory cannot be created.
    """
    fs = get_fs()
    try:
        if not path.is_dir():
            fs.make_dir(path, mode=DIR_MODE, parents=True)
        if not path.is_dir():
            if is_rehearsal():
                # The seam declined to create it; there is nothing to tighten.
                return
            # make_dir leaves an existing entry alone, so a file squatting on
            # the name is only noticed here.
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(path))
        if stat.S_IMODE(path.stat().st_mode) != DIR_MODE:
            fs.chmod(path, DIR_MODE)
    except OSError as exc:
        raise SecurityError(
            f"Cannot create the Noust web state directory {path}",
            details=(
                "The web panel stores its signing key, sessions and audit log there. "
                f"Create it as root with 'install -d -m 700 {path}', or point the panel "
                f"somewhere writable with {STATE_DIR_ENV}=/path/to/dir."
            ),
        ) from exc


def state_file_exists(path: Path) -> bool:
    """
    Report whether a file of the state directory exists.

    ``Path.exists`` treats only "not found" as absence and raises
    ``PermissionError`` when the directory cannot be searched, which is what a
    user other than root meets in a root-owned ``/etc/noust``. That is not an
    answer to "is there a token"; it is the same refusal every other state
    file gives, so it ends the same way.

    Args:
        path: A file under the state directory.

    Returns:
        True when the file exists.

    Raises:
        SecurityError: When the directory cannot be searched.
    """
    try:
        return path.exists()
    except OSError as exc:
        raise SecurityError(
            f"Cannot look for {path}",
            details=(
                "The web panel keeps its credentials where only its owner can read. "
                f"Run as root, or set {STATE_DIR_ENV} to a directory you own."
            ),
            output=str(exc),
        ) from exc


def write_private_file(path: Path, content: str) -> None:
    """
    Write a file that only its owner can read.

    Through the filesystem seam, which writes beside the destination and
    renames over it with the mode set at creation: a running console
    re-reads the signing key and the token hash whenever they change (see
    :class:`TokenManager`), and a truncate-then-write would let it read the
    empty file in between. Under ``--dry-run`` nothing is written; the
    caller keeps what it meant to write in memory for the rest of the run.

    Args:
        path: Destination file.
        content: Text to write.

    Raises:
        SecurityError: When the file cannot be written.
    """
    ensure_state_dir(path.parent)
    try:
        get_fs().write_text(path, content, mode=FILE_MODE)
    except OSError as exc:
        raise SecurityError(
            f"Cannot write {path}",
            details=(
                "Run the web panel as root, or set "
                f"{STATE_DIR_ENV} to a directory the current user owns."
            ),
        ) from exc


def _generation(secret_material: str) -> str:
    """
    Digest a secret into a label that says which issue of it is in force.

    Args:
        secret_material: A stored hash or a signing key.

    Returns:
        The first 16 hex characters of its SHA-256: enough to tell two issues
        apart, and nothing a caller could turn back into the material.
    """
    return hashlib.sha256(secret_material.encode()).hexdigest()[:16]


def _file_stamp(path: Path) -> tuple[int, int, int, int] | None:
    """
    Identify one version of a state file without reading it.

    Args:
        path: The file to identify.

    Returns:
        Inode, size and modification and change times, which together change
        on every :func:`write_private_file` (it renames a new file into
        place), or None when the file cannot be stat'ed.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass
class FailedAttempt:
    """
    Failed authentication attempts recorded for one client.

    Attributes:
        count: Failures inside the current window.
        first_attempt: Monotonic-ish timestamp of the first failure.
        locked_until: Timestamp until which the client is locked out.
    """

    count: int = 0
    first_attempt: float = 0.0
    locked_until: float = 0.0


class RateLimiter:
    """
    Sliding window request limiter.

    Keyed by whatever the caller counts by: the server keeps one keyed by
    client IP for anonymous requests and one keyed by credential for
    authenticated ones (see :func:`rate_limit_identity`).
    """

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window: int = RATE_LIMIT_WINDOW,
        max_tracked: int = RATE_LIMIT_MAX_TRACKED_IPS,
    ) -> None:
        """
        Build a rate limiter.

        Args:
            max_requests: Requests allowed per window and client.
            window: Window length in seconds.
            max_tracked: Maximum number of clients kept in memory.
        """
        self.max_requests = max_requests
        self.window = window
        self.max_tracked = max_tracked
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, client_ip: str) -> bool:
        """
        Record a request and report whether it is within the limit.

        Args:
            client_ip: The client's IP address.

        Returns:
            True when the request is allowed, False when rate limited.
        """
        now = time.time()
        with self._lock:
            self._purge(now)
            timestamps = [ts for ts in self._requests.get(client_ip, []) if now - ts < self.window]
            if len(timestamps) >= self.max_requests:
                self._requests[client_ip] = timestamps
                return False
            timestamps.append(now)
            self._requests[client_ip] = timestamps
            self._enforce_capacity()
            return True

    def get_remaining(self, client_ip: str) -> int:
        """
        Report how many requests the client has left in the window.

        Args:
            client_ip: The client's IP address.

        Returns:
            The number of remaining requests.
        """
        now = time.time()
        with self._lock:
            timestamps = [ts for ts in self._requests.get(client_ip, []) if now - ts < self.window]
            return max(0, self.max_requests - len(timestamps))

    def retry_after(self, client_ip: str) -> int:
        """
        Report how long until the client may make its next request.

        Args:
            client_ip: The client's key.

        Returns:
            Whole seconds until the oldest request in the window expires,
            at least 1; 0 when the client is within its budget.
        """
        now = time.time()
        with self._lock:
            timestamps = [ts for ts in self._requests.get(client_ip, []) if now - ts < self.window]
        if len(timestamps) < self.max_requests:
            return 0
        if not timestamps:
            # A budget of zero: nothing ever frees a slot.
            return self.window
        # The request whose expiry brings the count back under the budget.
        freeing = timestamps[len(timestamps) - self.max_requests]
        return max(1, math.ceil(freeing + self.window - now))

    def reset(self, client_ip: str) -> None:
        """
        Forget the history of one client.

        Args:
            client_ip: The client's IP address.
        """
        with self._lock:
            self._requests.pop(client_ip, None)

    def tracked_clients(self) -> int:
        """
        Report how many clients are currently tracked.

        Returns:
            Number of client entries held in memory.
        """
        with self._lock:
            return len(self._requests)

    def _purge(self, now: float) -> None:
        """
        Drop clients with no recent activity.

        Args:
            now: Current timestamp.
        """
        stale = [
            ip
            for ip, timestamps in self._requests.items()
            if not timestamps or now - timestamps[-1] >= self.window
        ]
        for ip in stale:
            del self._requests[ip]

    def _enforce_capacity(self) -> None:
        """Drop the least recently seen clients once the table is full."""
        # Spoofed sources can create entries faster than the window expires them.
        if len(self._requests) <= self.max_tracked:
            return
        oldest = sorted(self._requests, key=lambda ip: self._requests[ip][-1])
        for ip in oldest[: len(self._requests) - self.max_tracked]:
            del self._requests[ip]


class ConnectionLimiter:
    """
    Counts the long-lived connections each credential holds open.

    Keyed by :func:`credential_key`, not by address: the cost being bounded -
    a process and a socket per stream - is spent on behalf of a credential,
    and one script at its limit must not stop the operator's console from
    opening its own.
    """

    def __init__(self, limit: int = WS_MAX_PER_CREDENTIAL) -> None:
        """
        Args:
            limit: Connections allowed per credential at once.
        """
        self.limit = limit
        self._open: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> bool:
        """
        Take a place for one more connection, if there is one.

        Args:
            key: The credential's key.

        Returns:
            True when the connection may open; the caller must
            :meth:`release` it when it closes. False when the credential is
            already at its limit.
        """
        with self._lock:
            held = self._open.get(key, 0)
            if held >= self.limit:
                return False
            self._open[key] = held + 1
            return True

    def release(self, key: str) -> None:
        """
        Give back the place a closed connection held.

        Args:
            key: The credential's key, as passed to :meth:`acquire`.
        """
        with self._lock:
            held = self._open.get(key, 0) - 1
            if held > 0:
                self._open[key] = held
            else:
                self._open.pop(key, None)

    def held(self, key: str) -> int:
        """
        Args:
            key: The credential's key.

        Returns:
            How many connections it holds open now.
        """
        with self._lock:
            return self._open.get(key, 0)


class BruteForceProtection:
    """Lockout of clients that keep failing authentication."""

    def __init__(
        self,
        max_attempts: int = MAX_FAILED_ATTEMPTS,
        lockout_duration: int = LOCKOUT_DURATION,
        max_tracked: int = RATE_LIMIT_MAX_TRACKED_IPS,
    ) -> None:
        """
        Build the lockout tracker.

        Args:
            max_attempts: Failures tolerated before locking a client out.
            lockout_duration: Lockout length in seconds.
            max_tracked: Maximum number of clients kept in memory.
        """
        self.max_attempts = max_attempts
        self.lockout_duration = lockout_duration
        self.max_tracked = max_tracked
        self._failed_attempts: dict[str, FailedAttempt] = {}
        self._lock = threading.Lock()

    def record_failure(self, client_ip: str) -> None:
        """
        Record one failed authentication.

        Args:
            client_ip: The client's IP address.
        """
        now = time.time()
        with self._lock:
            self._purge(now)
            attempt = self._failed_attempts.get(client_ip)
            if attempt is None:
                self._failed_attempts[client_ip] = FailedAttempt(count=1, first_attempt=now)
                return

            if now - attempt.first_attempt > self.lockout_duration:
                attempt.count = 1
                attempt.first_attempt = now
                attempt.locked_until = 0.0
                return

            attempt.count += 1
            if attempt.count >= self.max_attempts:
                attempt.locked_until = now + self.lockout_duration

    def record_success(self, client_ip: str) -> None:
        """
        Clear the failure history of a client after a successful login.

        Args:
            client_ip: The client's IP address.
        """
        with self._lock:
            self._failed_attempts.pop(client_ip, None)

    def is_locked(self, client_ip: str) -> bool:
        """
        Report whether a client is currently locked out.

        Args:
            client_ip: The client's IP address.

        Returns:
            True while the lockout is in force.
        """
        with self._lock:
            attempt = self._failed_attempts.get(client_ip)
            return bool(attempt and attempt.locked_until > time.time())

    def get_lockout_remaining(self, client_ip: str) -> int:
        """
        Report the remaining lockout time.

        Args:
            client_ip: The client's IP address.

        Returns:
            Seconds left, zero when not locked out.
        """
        with self._lock:
            attempt = self._failed_attempts.get(client_ip)
            if not attempt:
                return 0
            return max(0, int(attempt.locked_until - time.time()))

    def get_attempts_remaining(self, client_ip: str) -> int:
        """
        Report how many failures the client has left.

        Args:
            client_ip: The client's IP address.

        Returns:
            Remaining attempts before lockout.
        """
        with self._lock:
            attempt = self._failed_attempts.get(client_ip)
            if not attempt:
                return self.max_attempts
            return max(0, self.max_attempts - attempt.count)

    def _purge(self, now: float) -> None:
        """
        Drop expired lockouts and cap the tracked client count.

        Args:
            now: Current timestamp.
        """
        stale = [
            ip
            for ip, attempt in self._failed_attempts.items()
            if now - attempt.first_attempt > self.lockout_duration and attempt.locked_until <= now
        ]
        for ip in stale:
            del self._failed_attempts[ip]

        if len(self._failed_attempts) > self.max_tracked:
            oldest = sorted(
                self._failed_attempts, key=lambda ip: self._failed_attempts[ip].first_attempt
            )
            for ip in oldest[: len(self._failed_attempts) - self.max_tracked]:
                del self._failed_attempts[ip]


#: The API token table, named by ``{table}`` so the rebuild that widens an old
#: CHECK (:meth:`SessionStore._allow_fleet_scope`) declares exactly what a
#: fresh database does. The token itself is never stored: only its salted
#: hash, exactly like the master token. Names stay unique across revocations
#: so an audit line naming a token always names one thing.
API_TOKENS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS {table} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL CHECK (scope IN ('read', 'deploy', 'admin', 'fleet')),
        created_at REAL NOT NULL,
        expires_at REAL,
        last_used_at REAL,
        revoked_at REAL,
        owner_account_id INTEGER,
        permissions TEXT,
        allowed_cidrs TEXT,
        allow_elevated INTEGER,
        created_by TEXT,
        last_used_ip TEXT
    )
"""

#: Columns 3.1 added to ``api_tokens``, for a database a 3.0 console created.
#: ``owner_account_id`` NULL is a token issued before its server had
#: accounts: it keeps the power of its scope until the first ``admin``
#: account adopts it. ``permissions`` NULL means "what the scope says".
#: ``allow_elevated`` NULL keeps 3.0's rule, where a token was never asked
#: for sudo mode; a token issued since says so explicitly.
API_TOKEN_COLUMNS_3_1 = (
    ("owner_account_id", "INTEGER"),
    ("permissions", "TEXT"),
    ("allowed_cidrs", "TEXT"),
    ("allow_elevated", "INTEGER"),
    ("created_by", "TEXT"),
    ("last_used_ip", "TEXT"),
)


#: What no API token holds under the ENS profile, whoever owns it: a token is
#: a standing credential nobody watches, and root-equivalent changes or
#: governing accounts and security settings need a person (ens.md §4.2.6).
#: The most a token issued before accounts holds once accounts exist and no
#: admin adopted it: an admin's permissions, never the governance of
#: accounts, security settings or the audit trail.
LEGACY_TOKEN_CEILING = permissions_for_role("admin") - {
    Permission.SECURITY_MANAGE,
    Permission.ACCOUNTS_MANAGE,
    Permission.AUDIT_MANAGE,
}

ENS_TOKEN_EXCLUDED = frozenset(
    {Permission.ROOT_EQUIVALENT, Permission.ACCOUNTS_MANAGE, Permission.SECURITY_MANAGE}
)


def _validated_networks(entries: list[str] | None) -> list[str] | None:
    """
    Check the networks a token is restricted to.

    Args:
        entries: Addresses or CIDRs, or None for no restriction.

    Returns:
        The networks in canonical form, or None when there are none.

    Raises:
        SecurityError: When an entry is not an address or a network.
    """
    if not entries:
        return None
    networks = []
    for entry in entries:
        try:
            networks.append(str(ipaddress.ip_network(str(entry).strip(), strict=False)))
        except ValueError as exc:
            raise SecurityError(
                f"Not an address or a network: {entry!r}",
                details="Use addresses or CIDRs, such as 203.0.113.7 or 10.0.0.0/8.",
            ) from exc
    return networks


def _json_list(raw: Any) -> list[str] | None:
    """
    Args:
        raw: A JSON list as stored, or NULL.

    Returns:
        Its items as strings, or None for NULL or anything unreadable.
    """
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return [str(item) for item in value] if isinstance(value, list) else None


def _token_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    Turn an ``api_tokens`` row into the record callers see, hash excluded.

    Args:
        row: A row of ``api_tokens``.

    Returns:
        The record, with its JSON columns decoded and ``allow_elevated`` as a
        boolean or None.
    """
    record = dict(row)
    record.pop("token_hash", None)
    record["permissions"] = _json_list(record.get("permissions"))
    record["allowed_cidrs"] = _json_list(record.get("allowed_cidrs"))
    elevated = record.get("allow_elevated")
    record["allow_elevated"] = None if elevated is None else bool(elevated)
    return record


class SessionStore:
    """
    Durable session and WebSocket ticket storage.

    Sessions live in SQLite rather than in a process-local set so that they
    survive a restart, work with more than one worker, and can be purged.
    """

    def __init__(self, path: Path) -> None:
        """
        Open (creating if needed) the session database.

        Args:
            path: Database file path.

        Raises:
            SecurityError: When the database cannot be created.
        """
        self.path = path
        try:
            if is_rehearsal():
                self._conn = self._rehearsal_copy(path)
            else:
                ensure_state_dir(path.parent)
                self._conn = sqlite3.connect(str(path), check_same_thread=False)
                os.chmod(path, FILE_MODE)
        except (sqlite3.Error, OSError) as exc:
            raise SecurityError(
                f"Cannot open the web session database {path}",
                details=(
                    "Run the web panel as root, or set "
                    f"{STATE_DIR_ENV} to a directory the current user owns."
                ),
            ) from exc
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_schema()

    @staticmethod
    def _rehearsal_copy(path: Path) -> sqlite3.Connection:
        """
        Open a private, in-memory copy of the database for a ``--dry-run``.

        SQLite writes past the filesystem seam, so a rehearsal cannot open the
        real file: ``noust --dry-run token create`` would issue a live token,
        and on a fresh machine simply connecting creates the file. The copy
        answers reads with the real state and forgets every write when the
        process ends, which is what a rehearsal promises.

        Args:
            path: The database file, which may not exist.

        Returns:
            An in-memory connection holding whatever the file held.

        Raises:
            sqlite3.Error: When the existing file cannot be read.
        """
        memory = sqlite3.connect(":memory:", check_same_thread=False)
        if path.is_file():
            source = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
            try:
                source.backup(memory)
            finally:
                source.close()
        return memory

    def _create_schema(self) -> None:
        """Create the session and ticket tables when missing, and migrate old ones."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    sid TEXT PRIMARY KEY,
                    csrf_token TEXT NOT NULL,
                    client_ip TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    created_at REAL NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "created_at" not in columns:
                # Databases written before absolute expiry existed: the safest
                # reading of an unknown birth date is "when it was last issued".
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN created_at REAL NOT NULL DEFAULT 0"
                )
                self._conn.execute("UPDATE sessions SET created_at = issued_at")
            if "elevated_until" not in columns:
                # NULL by default: a session predating sudo mode, like a
                # freshly created one, has not confirmed anything yet.
                self._conn.execute("ALTER TABLE sessions ADD COLUMN elevated_until REAL")
            if "family" not in columns:
                # The login a session descends from. Renewal replaces the sid,
                # so the sid alone cannot say "this is still the same sign-in"
                # - which is what a stream opened before the renewal, and a
                # logout that must also end the predecessor, need to ask.
                self._conn.execute("ALTER TABLE sessions ADD COLUMN family TEXT")
                self._conn.execute("UPDATE sessions SET family = sid")
            if "rotated_to" not in columns:
                # The one successor a renewal minted. Set once, so a retired
                # sid in its grace period re-issues that successor instead of
                # minting another on every request still carrying it.
                self._conn.execute("ALTER TABLE sessions ADD COLUMN rotated_to TEXT")
            if "kind" not in columns:
                # Every session before 3.1 was the master token signed in; the
                # default says so, so an operator signed in across the upgrade
                # stays signed in with exactly what they had.
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'master'"
                )
            if "account_id" not in columns:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN account_id INTEGER")
            if "auth_method" not in columns:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN auth_method TEXT")
            if "last_activity" not in columns:
                # NULL reads as the last issue: a session that predates the
                # idle timer has been idle since it was last renewed at most.
                self._conn.execute("ALTER TABLE sessions ADD COLUMN last_activity REAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ws_tickets (
                    ticket_hash TEXT PRIMARY KEY,
                    sid TEXT NOT NULL,
                    client_ip TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            # The second step of an account's sign-in: like a ticket, only the
            # hash of the value is kept, and it is spent by the step it opens.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS login_challenges (
                    challenge_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    client_ip TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    failures INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # The token itself is never stored: only its salted hash, exactly
            # like the master token. Names stay unique across revocations so
            # an audit line naming a token always names one thing.
            self._conn.execute(API_TOKENS_TABLE_SQL.format(table="api_tokens"))
            self._allow_fleet_scope()
            token_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(api_tokens)").fetchall()
            }
            for column, declaration in API_TOKEN_COLUMNS_3_1:
                if column not in token_columns:
                    self._conn.execute(f"ALTER TABLE api_tokens ADD COLUMN {column} {declaration}")

    def _allow_fleet_scope(self) -> None:
        """
        Rebuild an ``api_tokens`` table whose CHECK predates the ``fleet`` scope.

        SQLite cannot alter a CHECK constraint, so the table is copied into
        one declared the current way, ids and all, inside one transaction:
        a crash leaves either the old table or the new one, never neither.
        The caller holds the lock and the connection's context.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'api_tokens'"
        ).fetchone()
        if row is None or f"'{FLEET_SCOPE}'" in str(row["sql"]):
            return
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN")
        self._conn.execute("DROP TABLE IF EXISTS api_tokens_fleet")
        self._conn.execute(API_TOKENS_TABLE_SQL.format(table="api_tokens_fleet"))
        self._conn.execute(
            "INSERT INTO api_tokens_fleet (id, name, token_hash, scope, created_at, expires_at, "
            "last_used_at, revoked_at) SELECT id, name, token_hash, scope, created_at, "
            "expires_at, last_used_at, revoked_at FROM api_tokens"
        )
        self._conn.execute("DROP TABLE api_tokens")
        # RENAME carries the AUTOINCREMENT counter with it, so an id a
        # revoked token held is never handed out again.
        self._conn.execute("ALTER TABLE api_tokens_fleet RENAME TO api_tokens")

    def create(
        self,
        sid: str,
        csrf_token: str,
        client_ip: str,
        expires_at: float,
        created_at: float | None = None,
        *,
        kind: str = SESSION_KIND_MASTER,
        account_id: int | None = None,
        auth_method: str | None = None,
        last_activity: float | None = None,
    ) -> None:
        """
        Persist a new session.

        Args:
            sid: Session identifier.
            csrf_token: CSRF token bound to the session.
            client_ip: IP the session was issued to.
            expires_at: Expiry as a UNIX timestamp.
            created_at: Birth of the login this session descends from. Defaults
                to now; a rotation passes the original value so that renewal
                cannot extend the absolute lifetime.
            kind: ``account`` for a person's sign-in, ``master`` for the
                master token's.
            account_id: The account signed in, for an ``account`` session.
            auth_method: How it signed in: ``password``, ``token``, ``passkey``.
            last_activity: Start of the idle clock; now by default.
        """
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(sid, csrf_token, client_ip, issued_at, created_at, expires_at, revoked, family, "
                "kind, account_id, auth_method, last_activity) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                (
                    sid,
                    csrf_token,
                    client_ip,
                    now,
                    now if created_at is None else created_at,
                    expires_at,
                    sid,
                    kind,
                    account_id,
                    auth_method,
                    _now() if last_activity is None else last_activity,
                ),
            )
        self.purge_expired()

    def touch_activity(self, sid: str, at: float) -> None:
        """
        Record that a session was used, restarting its idle clock.

        Args:
            sid: Session identifier.
            at: The moment of use.
        """
        with self._lock, self._conn:
            self._conn.execute("UPDATE sessions SET last_activity = ? WHERE sid = ?", (at, sid))

    def revoke_account(self, account_id: int) -> int:
        """
        Revoke every session of one account and drop their tickets.

        Args:
            account_id: The account.

        Returns:
            Number of sessions revoked.
        """
        with self._lock, self._conn:
            sids = [
                str(row["sid"])
                for row in self._conn.execute(
                    "SELECT sid FROM sessions WHERE account_id = ? AND revoked = 0", (account_id,)
                ).fetchall()
            ]
            for sid in sids:
                self._conn.execute("UPDATE sessions SET revoked = 1 WHERE sid = ?", (sid,))
                self._conn.execute("DELETE FROM ws_tickets WHERE sid = ?", (sid,))
        return len(sids)

    def get(self, sid: str) -> dict[str, Any] | None:
        """
        Fetch a live session.

        Args:
            sid: Session identifier.

        Returns:
            The session row as a dict, or None when unknown, revoked or expired.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE sid = ? AND revoked = 0 AND expires_at > ?",
                (sid, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def extend(self, sid: str, expires_at: float) -> None:
        """
        Push a session's expiry further out.

        Args:
            sid: Session identifier.
            expires_at: New expiry as a UNIX timestamp.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE sid = ?", (expires_at, sid)
            )

    def set_elevated(self, sid: str, elevated_until: float | None) -> None:
        """
        Record or clear a session's sudo-mode confirmation.

        Args:
            sid: Session identifier.
            elevated_until: Timestamp the elevation expires at, or None to
                drop it, for example on logout.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET elevated_until = ? WHERE sid = ?", (elevated_until, sid)
            )

    def rotate(self, old_sid: str, new_sid: str, csrf_token: str, expires_at: float) -> dict | None:
        """
        Replace a session with a fresh identifier in one transaction.

        A session rotates once. The check and the write share the lock and
        the transaction, so two requests racing with the same retired cookie
        cannot both mint a successor.

        Args:
            old_sid: Session being retired.
            new_sid: Identifier of the replacement.
            csrf_token: CSRF token of the replacement.
            expires_at: Expiry of the replacement, as a UNIX timestamp.

        Returns:
            The row of the retired session, or None when it no longer exists
            or was already rotated - ask :meth:`get` for its ``rotated_to``.
        """
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE sid = ? AND revoked = 0 AND rotated_to IS NULL",
                (old_sid,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(sid, csrf_token, client_ip, issued_at, created_at, expires_at, revoked, "
                "elevated_until, family, kind, account_id, auth_method, last_activity) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
                (
                    new_sid,
                    csrf_token,
                    row["client_ip"],
                    now,
                    row["created_at"],
                    expires_at,
                    # Renewal must not reset the 10 minute window, only carry
                    # whatever is left of it to the new session id.
                    row["elevated_until"],
                    row["family"] or old_sid,
                    row["kind"],
                    row["account_id"],
                    row["auth_method"],
                    row["last_activity"],
                ),
            )
            # The retired identifier is not deleted outright: a dashboard fires
            # several requests at once and they all still carry the old cookie.
            # It is given a short grace instead, after which a captured copy of
            # the previous cookie is worthless.
            self._conn.execute(
                "UPDATE sessions SET expires_at = MIN(expires_at, ?), rotated_to = ? WHERE sid = ?",
                (now + SESSION_ROTATION_GRACE, new_sid, old_sid),
            )
        return dict(row)

    def family_alive(self, family: str) -> dict[str, Any] | None:
        """
        Find the live session of a sign-in, whatever its sid is by now.

        Args:
            family: The ``family`` of a session: the sid its login started
                with, inherited by every renewal.

        Returns:
            The most recently issued live row of that sign-in, or None when
            every session of it is revoked or expired.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE family = ? AND revoked = 0 AND expires_at > ? "
                "ORDER BY issued_at DESC LIMIT 1",
                (family, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def revoke(self, sid: str) -> None:
        """
        Revoke one sign-in: the session and every sid it renewed from or into.

        A renewal leaves its predecessor alive for a short grace; revoking
        only the current sid would leave that predecessor - and a stream
        opened with it - working after a logout.

        Args:
            sid: Session identifier, current or retired.
        """
        with self._lock, self._conn:
            row = self._conn.execute("SELECT family FROM sessions WHERE sid = ?", (sid,)).fetchone()
            family = row["family"] if row is not None and row["family"] else sid
            members = [
                str(member["sid"])
                for member in self._conn.execute(
                    "SELECT sid FROM sessions WHERE family = ? OR sid = ?", (family, sid)
                ).fetchall()
            ]
            for member in members or [sid]:
                self._conn.execute("UPDATE sessions SET revoked = 1 WHERE sid = ?", (member,))
                self._conn.execute("DELETE FROM ws_tickets WHERE sid = ?", (member,))

    def revoke_all(self) -> None:
        """Revoke every session and drop every pending ticket."""
        with self._lock, self._conn:
            self._conn.execute("UPDATE sessions SET revoked = 1")
            self._conn.execute("DELETE FROM ws_tickets")

    def revoke_all_except(self, keep_sid: str, account_id: int | None = None) -> int:
        """
        Revoke every session but one, and drop every pending ticket but its own.

        Args:
            keep_sid: Session identifier to leave untouched.
            account_id: Only the sessions of this account, when given: a
                person's "sign out everywhere else" is their own sessions,
                not everybody's.

        Returns:
            Number of sessions revoked.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT family FROM sessions WHERE sid = ?", (keep_sid,)
            ).fetchone()
            family = row["family"] if row is not None and row["family"] else keep_sid
            # The kept sign-in keeps its whole lineage: the predecessor still
            # in its grace is the same operator's requests in flight.
            if account_id is None:
                cursor = self._conn.execute(
                    "UPDATE sessions SET revoked = 1 "
                    "WHERE sid != ? AND COALESCE(family, sid) != ? AND revoked = 0",
                    (keep_sid, family),
                )
                self._conn.execute(
                    "DELETE FROM ws_tickets WHERE sid != ? AND sid NOT IN "
                    "(SELECT sid FROM sessions WHERE family = ?)",
                    (keep_sid, family),
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE sessions SET revoked = 1 WHERE sid != ? AND "
                    "COALESCE(family, sid) != ? AND revoked = 0 AND account_id = ?",
                    (keep_sid, family, account_id),
                )
                self._conn.execute(
                    "DELETE FROM ws_tickets WHERE sid IN (SELECT sid FROM sessions "
                    "WHERE revoked = 1 AND account_id = ?)",
                    (account_id,),
                )
        return cursor.rowcount

    def active_count(self) -> int:
        """
        Count sessions that are still usable.

        Returns:
            Number of live sessions.
        """
        with self._lock:
            # A renewed session's predecessor, alive for its grace period, is
            # the same sign-in, not another one.
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM sessions "
                "WHERE revoked = 0 AND expires_at > ? AND rotated_to IS NULL",
                (time.time(),),
            ).fetchone()
        return int(row["n"])

    def purge_expired(self) -> int:
        """
        Delete expired sessions, revoked sessions and expired tickets.

        Returns:
            Number of session rows removed.
        """
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ? OR revoked = 1", (now,)
            )
            self._conn.execute("DELETE FROM ws_tickets WHERE expires_at <= ?", (now,))
            self._conn.execute("DELETE FROM login_challenges WHERE expires_at <= ?", (now,))
        return cursor.rowcount

    def list_active(self, account_id: int | None = None) -> list[dict[str, Any]]:
        """
        List every session that is still usable, newest activity first.

        Args:
            account_id: Only this account's sessions, when given.

        Returns:
            The live session rows as dicts.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT sid, client_ip, issued_at, created_at, expires_at, kind, account_id, "
                "last_activity FROM sessions "
                "WHERE revoked = 0 AND expires_at > ? AND rotated_to IS NULL "
                "AND (? IS NULL OR account_id = ?) ORDER BY issued_at DESC",
                (time.time(), account_id, account_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def sids_with_prefix(self, prefix: str, account_id: int | None = None) -> list[str]:
        """
        Find the live sessions whose identifier starts with a prefix.

        Args:
            prefix: Leading characters of a session id. The caller has already
                validated it as hexadecimal, so it cannot carry LIKE wildcards.
            account_id: Only this account's sessions, when given.

        Returns:
            The matching session identifiers.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT sid FROM sessions WHERE revoked = 0 AND expires_at > ? "
                "AND rotated_to IS NULL AND sid LIKE ? AND (? IS NULL OR account_id = ?) "
                "ORDER BY sid",
                (time.time(), prefix + "%", account_id, account_id),
            ).fetchall()
        return [str(row["sid"]) for row in rows]

    def create_api_token(
        self,
        name: str,
        token_hash: str,
        scope: str,
        created_at: float,
        expires_at: float | None,
        *,
        owner_account_id: int | None = None,
        permissions: list[str] | None = None,
        allowed_cidrs: list[str] | None = None,
        allow_elevated: bool | None = None,
        created_by: str | None = None,
    ) -> int:
        """
        Persist a new API token record.

        Args:
            name: Unique human-chosen name.
            token_hash: Salted hash of the token; the token itself never lands.
            scope: One of :data:`API_TOKEN_SCOPES`.
            created_at: Creation time as a UNIX timestamp.
            expires_at: Expiry as a UNIX timestamp, or None for no expiry.
            owner_account_id: The account it acts for; None for a token of a
                server without accounts, or a fleet token.
            permissions: What it may do at most, or None for its scope's.
            allowed_cidrs: Networks it is accepted from, or None for any.
            allow_elevated: Whether it may act where sudo mode is asked; None
                keeps 3.0's rule.
            created_by: Who issued it, for the record.

        Returns:
            The new record's id.

        Raises:
            sqlite3.IntegrityError: When the name is already taken.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO api_tokens (name, token_hash, scope, created_at, expires_at, "
                "owner_account_id, permissions, allowed_cidrs, allow_elevated, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    token_hash,
                    scope,
                    created_at,
                    expires_at,
                    owner_account_id,
                    None if permissions is None else json.dumps(sorted(permissions)),
                    None if allowed_cidrs is None else json.dumps(allowed_cidrs),
                    None if allow_elevated is None else int(allow_elevated),
                    created_by,
                ),
            )
        return int(cursor.lastrowid or 0)

    def adopt_unowned_tokens(self, account_id: int) -> int:
        """
        Give every token issued before accounts existed to one account.

        Fleet tokens are left alone: they belong to a central, not a person.

        Args:
            account_id: The account that adopts them, the first ``admin``.

        Returns:
            How many tokens it adopted.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE api_tokens SET owner_account_id = ? "
                "WHERE owner_account_id IS NULL AND scope != ?",
                (account_id, FLEET_SCOPE),
            )
        return cursor.rowcount

    def get_api_token_by_id(self, token_id: int) -> dict[str, Any] | None:
        """
        Fetch an API token record by id, without its hash.

        Args:
            token_id: The record's id.

        Returns:
            The record, revoked or not, or None.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_tokens WHERE id = ?", (token_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record.pop("token_hash", None)
        return record

    def revoke_account_tokens(self, account_id: int) -> int:
        """
        Revoke every live token of one account.

        Args:
            account_id: The owner.

        Returns:
            How many were revoked.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE api_tokens SET revoked_at = ? WHERE owner_account_id = ? "
                "AND revoked_at IS NULL",
                (time.time(), account_id),
            )
        return cursor.rowcount

    def get_api_token(self, token_hash: str) -> dict[str, Any] | None:
        """
        Fetch an unrevoked API token by its hash.

        Expiry is deliberately left to the caller: "expired" and "unknown" are
        the same refusal on the wire, but the caller decides both from one row.

        Args:
            token_hash: Salted hash of the presented token.

        Returns:
            The token row as a dict, or None when unknown or revoked.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def find_api_token(self, token_hash: str) -> dict[str, Any] | None:
        """
        Fetch an API token by its hash whatever its state, revoked included.

        Only for telling a retired credential apart from a guess after it
        failed; never for authenticating one.

        Args:
            token_hash: Salted hash of the presented token.

        Returns:
            The token row as a dict, or None when no token ever had that hash.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        return dict(row) if row else None

    def get_api_token_by_name(self, name: str) -> dict[str, Any] | None:
        """
        Fetch an unrevoked API token by its name.

        Names are unique across every token ever issued, so a name names one
        credential for good; a WebSocket ticket is bound to one this way.

        Args:
            name: The token's name.

        Returns:
            The token row as a dict, or None when unknown or revoked.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_tokens WHERE name = ? AND revoked_at IS NULL", (name,)
            ).fetchone()
        return dict(row) if row else None

    def touch_api_token(self, token_id: int, used_at: float, used_ip: str | None = None) -> None:
        """
        Record when a token last authenticated a request, and from where.

        Args:
            token_id: The token record's id.
            used_at: The moment of use, as a UNIX timestamp.
            used_ip: The address it came from.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE api_tokens SET last_used_at = ?, last_used_ip = COALESCE(?, last_used_ip) "
                "WHERE id = ?",
                (used_at, used_ip, token_id),
            )

    def list_api_tokens(self, owner_account_id: int | None = None) -> list[dict[str, Any]]:
        """
        List API token records, newest first, without the hashes.

        Args:
            owner_account_id: Only this account's tokens, when given.

        Returns:
            The token rows as dicts.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, scope, created_at, expires_at, last_used_at, revoked_at, "
                "owner_account_id, permissions, allowed_cidrs, allow_elevated, created_by, "
                "last_used_ip FROM api_tokens WHERE (? IS NULL OR owner_account_id = ?) "
                "ORDER BY created_at DESC, id DESC",
                (owner_account_id, owner_account_id),
            ).fetchall()
        return [_token_record(row) for row in rows]

    def revoke_api_token(self, token_id: int) -> str | None:
        """
        Revoke one API token.

        Args:
            token_id: The token record's id.

        Returns:
            The token's name, for the audit record, or None when no such
            record exists. Revoking an already revoked token is a no-op that
            still reports the name: the outcome the caller asked for holds.
        """
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT name FROM api_tokens WHERE id = ?", (token_id,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE api_tokens SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                (now, token_id),
            )
        return str(row["name"])

    def store_ticket(self, ticket_hash: str, sid: str, client_ip: str, expires_at: float) -> None:
        """
        Persist a single-use WebSocket ticket.

        Args:
            ticket_hash: Hash of the ticket value.
            sid: The ``sid`` of the credential the ticket was issued to: a
                session id, :data:`MASTER_SID` or ``token:<name>``.
            client_ip: IP the ticket was issued to.
            expires_at: Expiry as a UNIX timestamp.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO ws_tickets (ticket_hash, sid, client_ip, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (ticket_hash, sid, client_ip, expires_at),
            )

    def revoke_tickets(self, sid: str) -> None:
        """
        Spend every outstanding WebSocket ticket issued to one credential.

        Args:
            sid: The ``sid`` the tickets were issued to.
        """
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM ws_tickets WHERE sid = ?", (sid,))

    def consume_ticket(self, ticket_hash: str) -> dict[str, Any] | None:
        """
        Atomically redeem a WebSocket ticket.

        Args:
            ticket_hash: Hash of the presented ticket.

        Returns:
            The ticket row as a dict, or None when unknown or expired.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM ws_tickets WHERE ticket_hash = ? AND expires_at > ?",
                (ticket_hash, time.time()),
            ).fetchone()
            self._conn.execute("DELETE FROM ws_tickets WHERE ticket_hash = ?", (ticket_hash,))
        return dict(row) if row else None

    def store_login_challenge(
        self, challenge_hash: str, account_id: int, client_ip: str, expires_at: float
    ) -> None:
        """
        Persist the second step of an account's sign-in.

        Args:
            challenge_hash: Hash of the challenge value.
            account_id: The account whose password step passed.
            client_ip: The address it passed from.
            expires_at: Expiry as a UNIX timestamp.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO login_challenges "
                "(challenge_hash, account_id, client_ip, expires_at) VALUES (?, ?, ?, ?)",
                (challenge_hash, int(account_id), client_ip, expires_at),
            )

    def get_login_challenge(self, challenge_hash: str) -> dict[str, Any] | None:
        """
        Read a live second step without spending it.

        Args:
            challenge_hash: Hash of the presented challenge.

        Returns:
            Its row as a dict, or None when unknown or expired.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM login_challenges WHERE challenge_hash = ? AND expires_at > ?",
                (challenge_hash, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def fail_login_challenge(self, challenge_hash: str, limit: int) -> None:
        """
        Count a wrong code against a second step, spending it at ``limit``.

        Args:
            challenge_hash: Hash of the challenge.
            limit: Wrong codes it takes.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE login_challenges SET failures = failures + 1 WHERE challenge_hash = ?",
                (challenge_hash,),
            )
            self._conn.execute(
                "DELETE FROM login_challenges WHERE challenge_hash = ? AND failures >= ?",
                (challenge_hash, limit),
            )

    def spend_login_challenge(self, challenge_hash: str) -> bool:
        """
        Atomically spend a second step.

        Args:
            challenge_hash: Hash of the challenge.

        Returns:
            True when it was live and is now spent; False when another request
            spent it first, or it expired.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM login_challenges WHERE challenge_hash = ? AND expires_at > ?",
                (challenge_hash, time.time()),
            )
        return cursor.rowcount == 1

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()


@dataclass(frozen=True)
class IssuedSession:
    """
    A freshly created session.

    Attributes:
        token: Signed session token for the cookie or the Bearer header.
        session_id: Server-side identifier of the session.
        csrf_token: Token that must accompany cookie-authenticated mutations.
        expires_at: Expiry as a UNIX timestamp.
        max_age: Lifetime in seconds.
    """

    token: str
    session_id: str
    csrf_token: str
    expires_at: float
    max_age: int


class TokenManager:
    """
    Master token, signing key and session lifecycle.

    The signing key is generated once and persisted with mode 0600. It is never
    regenerated silently: a key that changes on restart invalidates every
    session and cannot be shared between workers.

    The files in the state directory are the only record of the master token
    and the key, and a running console answers from them, not from a copy it
    took at start. ``noust web token --new`` and ``--regenerate`` run in a
    process of their own; if the console kept the token it issued in memory,
    the token those commands retire would keep opening it until a restart -
    and the restart would issue yet another token. So the token hash is read
    on every verification and the key is re-read whenever its file changes.
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        """
        Load persistent state, creating it on first run.

        Args:
            config: Security configuration; a default one is used when omitted.

        Raises:
            SecurityError: When secrets or sessions cannot be persisted.
        """
        self.config = config or SecurityConfig()
        # Stamped before reading, so a rotation landing between the two makes
        # the next _signing_key() call read again instead of being missed.
        self._secret_stamp = _file_stamp(self.config.secret_file)
        self._secret_key: str = self._load_or_create_secret()
        if self._secret_stamp is None:
            self._secret_stamp = _file_stamp(self.config.secret_file)
        self.sessions = SessionStore(self.config.session_db)
        # Serialises read-modify-write cycles on the two-factor state file, so
        # two logins racing to consume the same backup code cannot both win.
        self._totp_lock = threading.Lock()
        #: The people who sign in. Their rows live in the store; sessions and
        #: tokens here refer to them by id and read them on every request, so
        #: disabling an account or changing its role applies at once.
        self.accounts = AccountManager(policy=self.config.auth_policy)

    # ------------------------------------------------------------- principals

    def policy(self) -> AuthPolicy:
        """
        Returns:
            The sign-in policy in force.
        """
        return self.config.policy()

    def accounts_exist(self) -> bool:
        """
        Report whether this server has any account yet.

        Returns:
            True once one exists. A store that cannot be read answers False,
            and says so in the log: the master token is how an operator gets
            back in, and it must not be narrowed by the fault it is needed for.
        """
        try:
            return self.accounts.any_exist()
        except (StoreError, sqlite3.Error) as exc:
            logger.error("Cannot read the accounts from the store: %s", exc)
            return False

    def master_grant(self) -> str:
        """
        How the master token holds the console right now.

        Returns:
            ``recovery`` under the ENS profile (account recovery only),
            ``break_glass`` once accounts exist, ``compat`` before (the whole
            console, exactly as in 3.0).
        """
        if self.policy().ens:
            return GRANT_RECOVERY
        return GRANT_BREAK_GLASS if self.accounts_exist() else GRANT_COMPAT

    def _account(self, account_id: Any) -> Account | None:
        """
        Read an account a session or a token refers to.

        Args:
            account_id: The id stored with the session or token.

        Returns:
            The account, or None when it is gone or the store cannot be read,
            which refuses the credential: it is not known to be still allowed.
        """
        if account_id is None:
            return None
        try:
            return self.accounts.get(int(account_id))
        except (StoreError, sqlite3.Error, ValueError) as exc:
            logger.error("Cannot read account %s from the store: %s", account_id, exc)
            return None

    def account_fields(self, account: Account) -> dict[str, Any]:
        """
        What a session payload says about the account behind it.

        Args:
            account: The account.

        Returns:
            Its id, name, role and permissions, the legacy scope those amount
            to, and whether it must enrol a second factor or accept the usage
            notice before anything else.
        """
        permissions = permissions_for_role(account.role)
        notice = self.policy().notice_version
        return {
            "account_id": account.id,
            "username": account.username,
            "role": account.role,
            "permissions": permissions,
            "scope": legacy_scope(permissions),
            "mfa_pending": not account.has_mfa,
            "notice_pending": notice is not None and account.notice_version != notice,
        }

    def _apply_principal(self, payload: dict[str, Any], record: Mapping[str, Any]) -> bool:
        """
        Fill a session payload with who it is and what it may do.

        Args:
            payload: The payload being built. Modified in place.
            record: The session row.

        Returns:
            False when the session's account can no longer sign in, which ends
            the session: disabling an account ends its sessions at once.
        """
        if record.get("kind") == SESSION_KIND_ACCOUNT:
            account = self._account(record.get("account_id"))
            if account is None or not account.can_sign_in():
                return False
            payload.update(self.account_fields(account))
            payload["elevation_exempt"] = False
            return True
        grant = self.master_grant()
        permissions = GRANT_PERMISSIONS[grant]
        payload.update(
            {
                "grant": grant,
                "permissions": permissions,
                "scope": legacy_scope(permissions),
                "elevation_exempt": False,
            }
        )
        return True

    def _idle_seconds(self) -> float:
        """
        Returns:
            How long a session may go unused, from the sign-in policy.
        """
        return float(self.policy().idle_minutes) * 60.0

    def _idle_expired(self, record: Mapping[str, Any]) -> bool:
        """
        Report whether a session has been unused for longer than the policy allows.

        Args:
            record: A session row.

        Returns:
            True when its last activity is older than the idle timeout.
        """
        last = record.get("last_activity") or record.get("issued_at") or 0.0
        return _now() - float(last) > self._idle_seconds()

    def _note_activity(self, record: Mapping[str, Any]) -> None:
        """
        Restart a session's idle clock, at most every few seconds.

        Args:
            record: The session row being used.
        """
        now = _now()
        last = record.get("last_activity") or record.get("issued_at") or 0.0
        if now - float(last) >= SESSION_ACTIVITY_THROTTLE:
            self.sessions.touch_activity(str(record["sid"]), now)

    def _load_or_create_secret(self) -> str:
        """
        Load the signing key, generating and persisting it on first run.

        Returns:
            The hex-encoded signing key.

        Raises:
            SecurityError: When the key cannot be read or written.
        """
        secret_file = self.config.secret_file
        # No exists() first: on Python 3.12 it answers False only for "not
        # found", and raises PermissionError when the directory cannot be
        # searched, which is exactly what an unprivileged user running against
        # a root-owned /etc/noust hits. Reading is the one question, and every
        # way it can fail except absence ends as the same actionable error.
        try:
            existing: str | None = secret_file.read_text().strip()
        except (FileNotFoundError, NotADirectoryError):
            # What exists() calls absent. A file in the way of the directory
            # is reported by write_private_file, with the directory's name.
            existing = None
        except OSError as exc:
            raise SecurityError(
                f"Cannot read the web signing key {secret_file}",
                details=(
                    "The web panel must run as the user that owns the key. "
                    f"Run as root, or set {STATE_DIR_ENV} to a directory you own."
                ),
                output=str(exc),
            ) from exc

        if existing is None:
            secret = secrets.token_hex(SECRET_KEY_LENGTH)
            write_private_file(secret_file, secret)
            return secret
        if existing:
            return existing
        # Overwriting a key file that exists but is empty would invalidate
        # every session without anyone asking for it, and would hide the
        # truncation (a full disk, an interrupted write, a bad restore).
        raise SecurityError(
            f"The web signing key {secret_file} exists but is empty",
            details=(
                "Noust refuses to invent a new key silently, because that logs every "
                "operator out and hides whatever truncated the file. Restore the file "
                f"from backup, or delete it with 'rm {secret_file}' to start over, "
                "which revokes all existing sessions on purpose."
            ),
        )

    def generate_master_token(self) -> str:
        """
        Issue a new master token, retiring the previous one everywhere.

        Only the hash is persisted, and every console verifies against it, so
        the previous token stops working in a running console on its next
        request, not on its next restart.

        Returns:
            The generated master token, which is shown to the operator once.

        Raises:
            SecurityError: When the token hash cannot be persisted.
        """
        # A master token issued before 3.0 starts with wasm_; it is verified
        # by its hash, so it keeps working until it is replaced.
        token = f"{paths.NAME}_{secrets.token_urlsafe(TOKEN_LENGTH)}"
        write_private_file(self.config.token_file, self._hash_token(token))
        # A ticket issued to the retired token would otherwise outlive it by
        # up to WS_TICKET_TTL seconds. The table is shared with a running
        # console, so this holds across processes too.
        self.sessions.revoke_tickets(MASTER_SID)
        return token

    def _signing_key(self) -> str:
        """
        The signing key in force, re-read when another process rotated it.

        ``noust web token --regenerate`` rewrites the key file from its own
        process. A console holding on to the key it loaded would refuse the
        token issued under the new key and keep signing sessions that the next
        start rejects. A stat per call is the price of noticing.

        Returns:
            The hex-encoded signing key.
        """
        secret_file = self.config.secret_file
        stamp = _file_stamp(secret_file)
        if stamp is None or stamp == self._secret_stamp:
            # A key file that vanished is not a rotation: the key in memory
            # stays in force until a new file appears, since inventing one
            # here is exactly the silent regeneration _load_or_create_secret
            # refuses.
            return self._secret_key
        # Recorded before reading so an unreadable or empty file is reported
        # once, not on every request; any repair changes the stamp again.
        self._secret_stamp = stamp
        try:
            key = secret_file.read_text().strip()
        except OSError as exc:
            logger.error("Cannot re-read the web signing key %s: %s", secret_file, exc)
            return self._secret_key
        if not key:
            logger.error(
                "The web signing key %s is empty; keeping the key already loaded", secret_file
            )
            return self._secret_key
        self._secret_key = key
        return key

    def _hash_token(self, token: str) -> str:
        """
        Hash a master token for storage.

        Args:
            token: The plaintext token.

        Returns:
            Hex digest of the token, salted with the signing key.
        """
        return hashlib.sha256((token + self._signing_key()).encode()).hexdigest()

    def _load_master_token_hash(self) -> str | None:
        """
        Read the stored master token hash.

        Returns:
            The stored hash, or None when absent or unreadable.
        """
        token_file = self.config.token_file
        try:
            if token_file.exists():
                return token_file.read_text().strip()
        except OSError as exc:
            logger.error("Cannot read master token hash %s: %s", token_file, exc)
        return None

    def verify_master_token(self, token: str) -> bool:
        """
        Verify a master token against the hash on record right now.

        Args:
            token: The token presented by the client.

        Returns:
            True when the token matches the stored hash. The file is read on
            every call, so a token issued by another process is accepted and
            the one it replaced is refused from the next request on.
        """
        return self.master_token_generation(token) is not None

    def master_token_generation(self, token: str) -> str | None:
        """
        Verify a master token and name which issue of it matched.

        A stream opened with the master token stays open long after the
        handshake; it is re-checked by comparing this value with
        :meth:`current_master_generation`, so ``noust web token --new`` ends
        it without the stream having to keep the token itself.

        Args:
            token: The token presented by the client.

        Returns:
            A short digest of the stored hash the token matched - not a
            credential, and useless for recovering one - or None when the
            token does not match.
        """
        if not token:
            return None

        stored_hash = self._load_master_token_hash()
        if not stored_hash:
            return None
        if not secrets.compare_digest(self._hash_token(token), stored_hash):
            return None
        return _generation(stored_hash)

    def current_master_generation(self) -> str | None:
        """
        Returns:
            The digest :meth:`master_token_generation` reports for the master
            token in force, or None when none has been issued.
        """
        stored_hash = self._load_master_token_hash()
        return _generation(stored_hash) if stored_hash else None

    def _key_generation(self) -> str:
        """
        Returns:
            A short digest of the signing key in force. API tokens are hashed
            with it, so ``--regenerate`` retires every one of them; a stream an
            API token opened is re-checked against this.
        """
        return _generation(self._signing_key())

    def credential_is_current(self, payload: Mapping[str, Any]) -> bool:
        """
        Re-check a credential that was verified earlier, at a handshake.

        Every stream calls this on its heartbeat, so revoking a token,
        rotating the master token, signing out or letting a session expire
        ends the streams it had already opened, not only the ones it opens
        next.

        Args:
            payload: The payload the credential authenticated, as
                :func:`check_credential` or a WebSocket ticket built it.

        Returns:
            True while the same credential would still be accepted. A
            session is judged by its sign-in (``family``), not its sid, so a
            renewal - which replaces the sid - does not end it. A payload of
            no known type is not current.
        """
        kind = payload.get("type")
        if kind == "master":
            generation = payload.get("generation")
            return generation is not None and generation == self.current_master_generation()

        if kind == "api_token":
            record = self.sessions.get_api_token_by_name(str(payload.get("token_name") or ""))
            if record is None or int(record["id"]) != payload.get("token_id"):
                return False
            expires_at = record["expires_at"]
            if expires_at is not None and float(expires_at) <= time.time():
                return False
            owner = record.get("owner_account_id")
            if owner is not None:
                account = self._account(owner)
                if account is None or not account.can_sign_in():
                    return False
            return payload.get("generation") == self._key_generation()

        if kind == "session":
            family = payload.get("family") or payload.get("sid")
            if not family:
                return False
            row = self.sessions.family_alive(str(family))
            if row is None or self._past_absolute_deadline(row) or self._idle_expired(row):
                return False
            if row.get("kind") == SESSION_KIND_ACCOUNT:
                account = self._account(row.get("account_id"))
                return account is not None and account.can_sign_in()
            return True

        return False

    # ------------------------------------------------------------------ TOTP

    def _read_totp_state(self) -> dict[str, Any]:
        """
        Read the two-factor state file.

        Returns:
            The stored state, or the disabled default when the file has never
            been written.

        Raises:
            SecurityError: When the file exists but cannot be read or parsed.
                Treating a corrupt file as "two-factor is off" would turn any
                truncation into a silent bypass of the second factor.
        """
        default: dict[str, Any] = {
            "enabled": False,
            "secret": "",
            "pending_secret": "",
            "backup_codes": [],
        }
        path = self.config.totp_file
        try:
            raw = path.read_text()
        except FileNotFoundError:
            return default
        except OSError as exc:
            raise SecurityError(
                f"Cannot read the two-factor state file {path}",
                details=(
                    "Run the web panel as the user that owns it, or delete the file to "
                    "turn two-factor authentication off on purpose."
                ),
            ) from exc
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SecurityError(
                f"The two-factor state file {path} is corrupt",
                details=(
                    "Noust refuses to guess whether two-factor authentication was on. "
                    f"Restore the file from backup, or delete it with 'rm {path}' to "
                    "disable the second factor on purpose."
                ),
            ) from exc
        default.update(state if isinstance(state, dict) else {})
        return default

    def _write_totp_state(self, state: dict[str, Any]) -> None:
        """
        Persist the two-factor state with owner-only permissions.

        Args:
            state: The state to write.
        """
        fs = get_fs()
        fs.write_text(self.config.totp_file, json.dumps(state), mode=SECRET_MODE)

    def _hash_backup_code(self, code: str) -> str:
        """
        Hash a backup code for storage, the way the master token is hashed.

        Args:
            code: The code, in whatever spacing the operator typed it.

        Returns:
            Hex digest of the normalised code, salted with the signing key.
        """
        compact = code.strip().lower().replace("-", "").replace(" ", "")
        return hashlib.sha256((compact + self._signing_key()).encode()).hexdigest()

    def totp_enabled(self) -> bool:
        """
        Report whether logins require a second factor.

        Returns:
            True when two-factor authentication is confirmed and active.
        """
        return bool(self._read_totp_state()["enabled"])

    def totp_status(self) -> dict[str, Any]:
        """
        Describe the two-factor state without exposing any secret.

        Returns:
            Whether it is enabled, whether an enrolment is pending, and how
            many backup codes remain unused.
        """
        state = self._read_totp_state()
        return {
            "enabled": bool(state["enabled"]),
            "pending": bool(state["pending_secret"]),
            "backup_codes_remaining": len(state["backup_codes"]),
        }

    def begin_totp_enrollment(self) -> str:
        """
        Generate a pending secret for enrolment. Nothing is enforced yet.

        Returns:
            The new secret, to be shown to the operator exactly once as a QR
            code and as text.

        Raises:
            SecurityError: When two-factor authentication is already enabled.
                Replacing an active secret without presenting a current code
                would let a hijacked session swap the operator's authenticator
                for its own.
        """
        with self._totp_lock:
            state = self._read_totp_state()
            if state["enabled"]:
                raise SecurityError(
                    "Two-factor authentication is already enabled",
                    details="Disable it with a current code before enrolling a new authenticator.",
                )
            secret = totp.generate_secret()
            state["pending_secret"] = secret
            self._write_totp_state(state)
        return secret

    def pending_totp_secret(self) -> str | None:
        """
        Return the secret of an enrolment that has been begun but not confirmed.

        Only the enrolment screen reads this, to re-show the QR when the first
        code typed was wrong. It is meaningless to an attacker who is not
        already inside an authenticated session, because nothing accepts codes
        derived from it until it is confirmed.

        Returns:
            The pending secret, or None when no enrolment is in progress.
        """
        return self._read_totp_state()["pending_secret"] or None

    def confirm_totp_enrollment(self, code: str) -> list[str] | None:
        """
        Verify a code against the pending secret and activate the second factor.

        Args:
            code: The six-digit code the authenticator app shows.

        Returns:
            The backup codes, in clear, to be shown exactly once - they are
            stored only as salted hashes. None when the code did not verify.

        Raises:
            SecurityError: When no enrolment is in progress.
        """
        with self._totp_lock:
            state = self._read_totp_state()
            pending = state["pending_secret"]
            if not pending:
                raise SecurityError(
                    "No two-factor enrolment is in progress",
                    details="Begin one first: POST /api/auth/2fa/enroll, or Enable in Settings.",
                )
            if not totp.verify(pending, code, t=_now()):
                return None
            codes = [
                f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(BACKUP_CODE_COUNT)
            ]
            state.update(
                {
                    "enabled": True,
                    "secret": pending,
                    "pending_secret": "",
                    "backup_codes": [self._hash_backup_code(c) for c in codes],
                    # Steps spent under a previous secret mean nothing for this one.
                    "last_steps": {},
                }
            )
            self._write_totp_state(state)
        return codes

    def verify_second_factor(self, code: str, purpose: str = "login") -> bool:
        """
        Check a second factor: a TOTP code, or a single-use backup code.

        A backup code that matches is consumed in the same locked cycle that
        verified it, so it cannot be replayed by a second login racing the
        first. A TOTP code is one-use too (RFC 6238, 5.2): the step it
        matched is remembered per purpose, and that step or any earlier one
        is refused for the same purpose afterwards. Without that, a code
        read over a shoulder stayed good for the rest of its window, up to
        ninety seconds with the drift allowance.

        Args:
            code: What the client typed.
            purpose: What the code is being spent on - ``login``, ``elevate``
                or ``disable``. Remembered separately so that signing in and
                then confirming a destructive action with the same code, each
                once, does not make the operator wait for the next one.

        Returns:
            True when the code is a current, unused TOTP value or an unused
            backup code. False otherwise, including when two-factor is not
            enabled: this method fails closed, and the caller decides whether
            a second factor was required at all.
        """
        if not code or not code.strip():
            return False
        with self._totp_lock:
            state = self._read_totp_state()
            if not state["enabled"]:
                return False
            step = totp.matched_step(state["secret"], code, t=_now())
            if step is not None:
                spent = state.get("last_steps")
                spent = dict(spent) if isinstance(spent, dict) else {}
                last = spent.get(purpose)
                if isinstance(last, int) and step <= last:
                    return False
                spent[purpose] = step
                state["last_steps"] = spent
                self._write_totp_state(state)
                return True
            presented = self._hash_backup_code(code)
            remaining = [
                stored
                for stored in state["backup_codes"]
                if not hmac.compare_digest(stored, presented)
            ]
            if len(remaining) != len(state["backup_codes"]):
                state["backup_codes"] = remaining
                self._write_totp_state(state)
                return True
            return False

    def regenerate_backup_codes(self) -> list[str]:
        """
        Replace the backup codes with a fresh set, invalidating the old ones.

        Unlike :meth:`disable_totp`, this does not ask for a current code: the
        caller is a cookie session that has just called ``POST
        /api/auth/elevate`` (D5), the same standing proof of identity that
        guards issuing an API token, and asking for a second, TOTP-specific
        proof on top of it would only make losing the authenticator - the
        situation this command exists for - harder to recover from.

        Returns:
            The new codes, in clear, to be shown exactly once - only their
            salted hashes are stored, so they cannot be shown again.

        Raises:
            SecurityError: When two-factor authentication is not enabled.
                There is nothing to regenerate for a login that needs no
                second factor.
        """
        with self._totp_lock:
            state = self._read_totp_state()
            if not state["enabled"]:
                raise SecurityError(
                    "Two-factor authentication is not enabled",
                    details="There is nothing to regenerate. Enrol first from Settings.",
                )
            codes = [
                f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(BACKUP_CODE_COUNT)
            ]
            state["backup_codes"] = [self._hash_backup_code(c) for c in codes]
            self._write_totp_state(state)
        return codes

    def disable_totp(self, code: str) -> bool:
        """
        Turn the second factor off, on presentation of a current code.

        Args:
            code: A TOTP code or an unused backup code.

        Returns:
            True when it was disabled. False when the code did not verify, in
            which case nothing changed.

        Raises:
            SecurityError: When two-factor authentication is not enabled.
        """
        if not self._read_totp_state()["enabled"]:
            raise SecurityError(
                "Two-factor authentication is not enabled",
                details="There is nothing to disable. Enrol first from Settings.",
            )
        if not self.verify_second_factor(code, purpose="disable"):
            return False
        with self._totp_lock:
            self._write_totp_state(
                {"enabled": False, "secret": "", "pending_secret": "", "backup_codes": []}
            )
        return True

    # ------------------------------------------------------------- API tokens

    def create_api_token(
        self,
        name: str,
        scope: str,
        expires_hours: float | None = None,
        *,
        owner: Account | None = None,
        permissions: list[str] | None = None,
        allowed_cidrs: list[str] | None = None,
        allow_elevated: bool = False,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Issue a named, scoped API token.

        The token is returned in clear exactly once; only its salted hash is
        stored, the same way the master token is stored.

        A token issued for an account acts as that account, with at most its
        permissions, checked again on every use: a token never outlives its
        owner's role or its owner being disabled. A token issued without an
        owner (the root CLI, or the master token before accounts exist) keeps
        its scope's power until the first ``admin`` account adopts it.

        Args:
            name: Human-chosen name, unique across all tokens ever issued.
            scope: One of :data:`API_TOKEN_SCOPES`.
            expires_hours: Lifetime in hours, or None for a token that only
                dies by revocation. Required under the ENS profile, at most
                its ``token_max_days``, and defaulting to it.
            owner: The account the token acts for.
            permissions: Permissions to narrow it to, instead of its scope's;
                every one must be held by the owner.
            allowed_cidrs: Networks it is accepted from; any when omitted.
            allow_elevated: Let it act where sudo mode is asked, which a
                token cannot open by itself. Refused under the ENS profile.
            created_by: Who issued it, for the record.

        Returns:
            The record, including the one and only clear copy of the token
            under ``"token"``.

        Raises:
            SecurityError: When the name is empty or taken, the scope is not
                a scope, is ``fleet`` (see :meth:`create_fleet_token`), the
                expiry is not positive or too long, a permission or network
                is not valid, or the owner does not hold a permission asked.
        """
        if scope == FLEET_SCOPE:
            # The chokepoint for the one scope that may speak for somebody
            # else: POST /api/auth/tokens and 'noust token create' both land
            # here, and neither may mint it.
            raise SecurityError(
                "A fleet token cannot be issued here",
                details=(
                    "Fleet tokens are created on this server by 'noust fleet authorize "
                    "--central-key ... --name <central>', which also installs the "
                    "central's restricted SSH key and prints the join code for it."
                ),
            )
        if scope not in API_TOKEN_SCOPES:
            raise SecurityError(
                f"Unknown API token scope: {scope!r}",
                details=f"Use one of: {', '.join(API_TOKEN_SCOPES)}.",
            )
        policy = self.policy()
        if owner is None and policy.ens:
            raise SecurityError(
                "Under the ENS profile an API token belongs to an account",
                details=(
                    "Issue it signed in as that account, or from the CLI with "
                    "'noust token create NAME --owner USERNAME'."
                ),
            )
        if policy.token_max_days is not None:
            longest = float(policy.token_max_days) * 24
            if expires_hours is None:
                expires_hours = longest
            elif expires_hours > longest:
                raise SecurityError(
                    f"API tokens last at most {policy.token_max_days} days under the ENS profile",
                    details=f"Ask for {int(longest)} hours or fewer, and issue a new one then.",
                )
            if allow_elevated:
                raise SecurityError(
                    "API tokens cannot act in sudo mode under the ENS profile",
                    details="Do what needs confirming from the console, as a person.",
                )
        stored_permissions: list[str] | None = None
        if owner is not None:
            held = permissions_for_role(owner.role)
            if permissions is None:
                granted = TOKEN_SCOPES[scope] & held
            else:
                unknown = sorted(set(permissions) - ALL_PERMISSIONS)
                if unknown:
                    raise SecurityError(
                        f"Unknown permissions: {', '.join(unknown)}",
                        details="GET /api/auth/roles lists every permission there is.",
                    )
                missing = sorted(set(permissions) - held)
                if missing:
                    raise SecurityError(
                        f"A token cannot hold more than its owner: {', '.join(missing)}",
                        details=f"The {owner.role} role does not hold them; leave them out.",
                    )
                granted = frozenset(permissions) | {Permission.SELF}
            stored_permissions = sorted(granted)
            scope = legacy_scope(granted)
        networks = _validated_networks(allowed_cidrs)
        return self._issue_api_token(
            name,
            scope,
            expires_hours,
            owner_account_id=owner.id if owner is not None else None,
            permissions=stored_permissions,
            allowed_cidrs=networks,
            # A token nobody owns is issued the 3.0 way, by the master token
            # or root, and keeps 3.0's rule until an admin adopts it; a
            # person's token is asked for sudo mode unless issued otherwise.
            allow_elevated=bool(allow_elevated) if owner is not None or allow_elevated else None,
            created_by=created_by,
        )

    def create_fleet_token(self, name: str) -> dict[str, Any]:
        """
        Issue the token a central holds for this server, with the ``fleet`` scope.

        Called only by ``noust fleet authorize``, which runs on this server as
        root; nothing reachable over HTTP issues one. It never expires: the
        node's operator ends it by revoking it.

        Args:
            name: ``fleet-<central>``, unique across all tokens ever issued.

        Returns:
            The record, as :meth:`create_api_token` returns it.

        Raises:
            SecurityError: When the name is empty, too long or taken.
        """
        return self._issue_api_token(name, FLEET_SCOPE, None)

    def _issue_api_token(
        self,
        name: str,
        scope: str,
        expires_hours: float | None,
        *,
        owner_account_id: int | None = None,
        permissions: list[str] | None = None,
        allowed_cidrs: list[str] | None = None,
        allow_elevated: bool | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Mint and store an API token whose scope the caller already vetted.

        Args:
            name: Human-chosen name, unique across all tokens ever issued.
            scope: The scope, already checked by the public caller.
            expires_hours: Lifetime in hours, or None.
            owner_account_id: The owner, or None.
            permissions: Its permissions, or None for its scope's.
            allowed_cidrs: Networks it is accepted from, or None.
            allow_elevated: Whether it may act in sudo mode; None for 3.0's rule.
            created_by: Who issued it.

        Returns:
            The record, including the one and only clear copy of the token.

        Raises:
            SecurityError: When the name is empty or taken, or the expiry is
                not positive.
        """
        cleaned = name.strip()
        if not cleaned or len(cleaned) > 64:
            raise SecurityError(
                "API token names are 1 to 64 characters",
                details="Name the token after what will hold it, such as 'ci-deploy'.",
            )
        if expires_hours is not None and expires_hours <= 0:
            raise SecurityError(
                "The token expiry must be a positive number of hours",
                details="Omit it entirely for a token that only dies by revocation.",
            )

        token = f"{API_TOKEN_PREFIX}{secrets.token_urlsafe(TOKEN_LENGTH)}"
        now = time.time()
        expires_at = now + float(expires_hours) * 3600 if expires_hours is not None else None
        try:
            token_id = self.sessions.create_api_token(
                cleaned,
                self._hash_token(token),
                scope,
                now,
                expires_at,
                owner_account_id=owner_account_id,
                permissions=permissions,
                allowed_cidrs=allowed_cidrs,
                allow_elevated=allow_elevated,
                created_by=created_by,
            )
        except sqlite3.IntegrityError as exc:
            raise SecurityError(
                f"An API token named {cleaned!r} already exists",
                details=(
                    "Names are unique, including revoked tokens, so an audit line "
                    "naming one always names one thing. Pick a new name."
                ),
            ) from exc

        return {
            "id": token_id,
            "name": cleaned,
            "scope": scope,
            "token": token,
            "created_at": now,
            "expires_at": expires_at,
            "owner_account_id": owner_account_id,
            "permissions": permissions,
            "allowed_cidrs": allowed_cidrs,
            "allow_elevated": allow_elevated,
        }

    def list_api_tokens(self, owner_account_id: int | None = None) -> list[dict[str, Any]]:
        """
        List API token records. No hash and no token is in the result.

        Args:
            owner_account_id: Only this account's tokens, when given.

        Returns:
            The records, newest first.
        """
        return self.sessions.list_api_tokens(owner_account_id)

    def get_api_token(self, token_id: int) -> dict[str, Any] | None:
        """
        Args:
            token_id: A token record's id.

        Returns:
            The record without its hash, or None.
        """
        return self.sessions.get_api_token_by_id(token_id)

    def adopt_unowned_tokens(self, account_id: int) -> int:
        """
        Hand every token issued before accounts existed to one account.

        Args:
            account_id: The first ``admin`` account.

        Returns:
            How many tokens it adopted.
        """
        return self.sessions.adopt_unowned_tokens(account_id)

    def end_account_access(self, account_id: int) -> tuple[int, int]:
        """
        Revoke every session and token of an account, for disabling or removing it.

        Args:
            account_id: The account.

        Returns:
            How many sessions and how many tokens were revoked.
        """
        return (
            self.sessions.revoke_account(account_id),
            self.sessions.revoke_account_tokens(account_id),
        )

    def revoke_api_token(self, token_id: int) -> str | None:
        """
        Revoke one API token by id.

        Args:
            token_id: The record's id, as listed.

        Returns:
            The token's name, or None when no such record exists.
        """
        return self.sessions.revoke_api_token(token_id)

    def verify_api_token(self, token: str, client_ip: str | None = None) -> dict[str, Any] | None:
        """
        Verify an API token and return the payload it authenticates.

        The lookup is by the salted hash of the presented value, so timing
        reveals nothing about any stored token, and the fetched row's hash is
        still compared in constant time. Success records ``last_used_at``, at
        most once per :data:`API_TOKEN_LAST_USED_THROTTLE` seconds.

        Args:
            token: The presented credential.
            client_ip: Address presenting it, recorded in the payload.

        Returns:
            The payload, carrying the token's scope, or None when the token is
            unknown, revoked or expired.
        """
        if not token.startswith(API_TOKEN_PREFIXES):
            return None

        presented = self._hash_token(token)
        record = self.sessions.get_api_token(presented)
        if record is None:
            return None
        if not hmac.compare_digest(str(record["token_hash"]), presented):
            return None

        return self._api_token_payload(record, client_ip)

    def retired_fleet_token_name(self, token: str) -> str | None:
        """
        Name the fleet token a failed credential was, if it was one.

        A central goes on presenting its token until it learns the node
        revoked it, and it arrives from loopback - the address the operator's
        own SSH-tunnelled console arrives from too. Knowing the value of a
        real token, even a retired one, is not guessing; the caller counts
        such a failure under the token instead of the address.

        Args:
            token: A credential that did not verify.

        Returns:
            The token's name when it is a ``fleet`` token that was issued
            here and is now revoked or expired, otherwise None.
        """
        if not token.startswith(API_TOKEN_PREFIXES):
            return None
        presented = self._hash_token(token)
        record = self.sessions.find_api_token(presented)
        if record is None or record["scope"] != FLEET_SCOPE:
            return None
        if not hmac.compare_digest(str(record["token_hash"]), presented):
            return None
        return str(record["name"])

    def _api_token_payload(
        self, record: Mapping[str, Any], client_ip: str | None
    ) -> dict[str, Any] | None:
        """
        Turn an unrevoked API token row into the payload it authenticates.

        Shared by a Bearer token and a WebSocket ticket issued to one, so the
        two cannot disagree about expiry or about the scope a token carries.

        Args:
            record: The token row, already known to be unrevoked.
            client_ip: Address presenting the credential.

        Returns:
            The payload, or None when the token has expired.
        """
        now = time.time()
        expires_at = record["expires_at"]
        if expires_at is not None and float(expires_at) <= now:
            return None

        payload: dict[str, Any] = {
            "type": "api_token",
            "sid": f"{API_TOKEN_SID_PREFIX}{record['name']}",
            "scope": str(record["scope"]),
            "ip": client_ip,
            "token_id": int(record["id"]),
            "token_name": str(record["name"]),
            "generation": self._key_generation(),
        }
        if record["scope"] != FLEET_SCOPE:
            payload.update(self._token_principal(record, client_ip))

        last_used = record["last_used_at"]
        if last_used is None or now - float(last_used) >= API_TOKEN_LAST_USED_THROTTLE:
            self.sessions.touch_api_token(int(record["id"]), now, client_ip)
        return payload

    def _token_principal(self, record: Mapping[str, Any], client_ip: str | None) -> dict[str, Any]:
        """
        Decide what an API token may do on this request.

        Args:
            record: The token's row, live and unexpired.
            client_ip: The address presenting it.

        Returns:
            The payload fields: permissions, the scope they amount to, the
            owner and its role, and whether sudo mode is not asked of it.

        Raises:
            CredentialRefused: 401 when it is used from outside its networks,
                its owner can no longer sign in, or it has no owner under the
                ENS profile. None of these is a guess, so none is counted.
        """
        policy = self.policy()
        cidrs = _json_list(record.get("allowed_cidrs"))
        if cidrs and not (client_ip and ip_matches(client_ip, cidrs)):
            raise CredentialRefused(
                401,
                "token_network",
                f"The API token {record['name']!r} is not accepted from this address.",
                "Use it from one of the networks it was issued for, or issue another.",
                action="auth.token.denied",
            )
        owner_id = record.get("owner_account_id")
        if owner_id is None:
            first_admin = self._first_admin()
            if first_admin is not None:
                # The first admin account adopts every token issued before
                # accounts existed, the moment one of them is next used: from
                # then on each acts as a person and within their role.
                self.sessions.adopt_unowned_tokens(first_admin.id)
                owner_id = first_admin.id
        stored = _json_list(record.get("permissions"))
        requested = (
            frozenset(stored)
            if stored is not None
            else LEGACY_SCOPES.get(str(record["scope"]), permissions_for_scope("read"))
        )
        role: str | None = None
        owner: Account | None = None
        if owner_id is None:
            if policy.ens:
                raise CredentialRefused(
                    401,
                    "legacy_token_refused",
                    f"The API token {record['name']!r} belongs to no account, which the "
                    "ENS profile does not accept.",
                    "Create an admin account (noust user create --role admin): it adopts "
                    "every token issued before, or issue a new token from an account.",
                    action="auth.token.denied",
                )
            permissions = requested
            if self._accounts_exist():
                # Accounts exist but no admin adopted it: a 3.0 token is not
                # above every person, so it holds at most what an admin does -
                # never the accounts, the security settings or the audit trail.
                permissions = requested & LEGACY_TOKEN_CEILING
        else:
            owner = self._account(owner_id)
            if owner is None or not owner.can_sign_in():
                raise CredentialRefused(
                    401,
                    "token_owner_inactive",
                    f"The account the API token {record['name']!r} belongs to cannot sign in.",
                    "Enable the account again, or issue a token from an active one.",
                    action="auth.token.denied",
                )
            role = owner.role
            permissions = requested & permissions_for_role(owner.role)
        if policy.ens:
            permissions = permissions - ENS_TOKEN_EXCLUDED
        allow_elevated = record.get("allow_elevated")
        notice = policy.notice_version
        return {
            "permissions": frozenset(permissions),
            "scope": legacy_scope(permissions),
            "role": role,
            "owner_account_id": owner_id,
            "elevation_exempt": (allow_elevated is None or bool(allow_elevated)) and not policy.ens,
            # A token acts for its owner, so it waits for its owner to accept
            # the usage notice as the owner's session does.
            "notice_pending": owner is not None
            and notice is not None
            and owner.notice_version != notice,
        }

    def _accounts_exist(self) -> bool:
        """
        Returns:
            True when any account exists, or when the store cannot be read:
            a token is not given more on a guess.
        """
        try:
            return self.accounts.any_exist()
        except (StoreError, sqlite3.Error) as exc:
            logger.error("Cannot read the accounts from the store: %s", exc)
            return True

    def _first_admin(self) -> Account | None:
        """
        Returns:
            The oldest usable ``admin`` account, or None when there is none
            or the store cannot be read.
        """
        try:
            return self.accounts.first_admin()
        except (StoreError, sqlite3.Error) as exc:
            logger.error("Cannot read the accounts from the store: %s", exc)
            return None

    # ---------------------------------------------------------------- sessions

    def create_session(
        self,
        client_ip: str,
        *,
        account_id: int | None = None,
        auth_method: str = "token",
    ) -> IssuedSession:
        """
        Create and persist a session.

        Args:
            client_ip: IP the session is issued to.
            account_id: The account signing in; None for the master token.
            auth_method: How it signed in: ``token`` (the master token),
                ``password`` or ``passkey``.

        Returns:
            The issued session, including its CSRF token.

        Raises:
            IncidentLockdownError: The console is locked down for an incident
                (``noust incident freeze``) and this is an account; the
                master token, the break-glass way in, still signs in.
        """
        from noust.core.ens.incident import refuse_new_session

        refuse_new_session(account_id=account_id, client_ip=client_ip)
        now = utcnow()
        max_age = int(min(self.config.token_expiration_hours * 3600, self._absolute_seconds()))
        expires = now + timedelta(seconds=max_age)
        session_id = secrets.token_hex(16)
        csrf_token = secrets.token_urlsafe(32)

        self.sessions.create(
            session_id,
            csrf_token,
            client_ip,
            expires.timestamp(),
            kind=SESSION_KIND_ACCOUNT if account_id is not None else SESSION_KIND_MASTER,
            account_id=account_id,
            auth_method=auth_method,
        )
        token = self._encode(session_id, client_ip, now, expires)

        return IssuedSession(
            token=token,
            session_id=session_id,
            csrf_token=csrf_token,
            expires_at=expires.timestamp(),
            max_age=max_age,
        )

    def _encode(self, session_id: str, client_ip: str, issued: datetime, expires: datetime) -> str:
        """
        Sign a session identifier.

        The token is the opaque identifier plus an HMAC of it, not a JWT. Every
        field the server trusts - expiry, address, CSRF token - is read from the
        session record, never from the token, so a JWT's payload was carried
        across the wire and then ignored. What is left is "did we issue this",
        which one HMAC answers.

        Dropping it also drops ``python-jose``, which is not packaged in Ubuntu
        26.04 and would have left the panel uninstallable there.

        Args:
            session_id: Server-side session identifier.
            client_ip: IP the session belongs to. Unused in the token itself;
                it is checked against the stored record.
            issued: Issue time. Recorded in the store.
            expires: Expiry time. Recorded in the store.

        Returns:
            The signed token.
        """
        signature = hmac.new(
            self._signing_key().encode(), session_id.encode(), hashlib.sha256
        ).hexdigest()
        return f"{session_id}.{signature}"

    def _decode(self, token: str) -> str | None:
        """
        Return the session identifier a token carries, if we signed it.

        Args:
            token: The token presented by the client.

        Returns:
            The session identifier, or None when the token is malformed or the
            signature does not match.
        """
        session_id, _, signature = token.partition(".")
        if not session_id or not signature:
            return None
        expected = hmac.new(
            self._signing_key().encode(), session_id.encode(), hashlib.sha256
        ).hexdigest()
        # Constant time: a timing oracle here would let an attacker forge a
        # signature one byte at a time.
        if not hmac.compare_digest(expected, signature):
            return None
        return session_id

    def signed_session_token(self, token: str) -> bool:
        """
        Report whether a value is a session token this server signed.

        Says nothing about whether the session is still alive; see
        :meth:`verify_session_token` for that.

        Args:
            token: The presented value.

        Returns:
            True when its signature verifies under the signing key in force.
        """
        return bool(token) and self._decode(token) is not None

    def verify_session_token(
        self, token: str, client_ip: str | None = None
    ) -> dict[str, Any] | None:
        """
        Verify a session token and return its payload.

        Args:
            token: The signed session token.
            client_ip: Address the token is being presented from. When set and
                ``bind_session_to_ip`` is on, a stolen token is useless from a
                different address.

        Returns:
            The payload, extended with the session's CSRF token, or None when
            the token is invalid, revoked, expired or presented from the wrong
            address.
        """
        if not token:
            return None

        session_id = self._decode(token)
        if not session_id:
            return None

        record = self.sessions.get(session_id)
        if record is None:
            return None

        payload: dict[str, Any] = {
            "sub": SESSION_SUBJECT,
            "sid": session_id,
            "ip": record["client_ip"],
            "exp": record["expires_at"],
            "iss": SESSION_ISSUER,
        }

        if self._past_absolute_deadline(record) or self._idle_expired(record):
            self.sessions.revoke(session_id)
            return None

        if self.config.bind_session_to_ip and client_ip and record["client_ip"] != client_ip:
            return None

        payload["csrf"] = record["csrf_token"]
        payload["expires_at"] = record["expires_at"]
        payload["family"] = record.get("family") or session_id
        payload["type"] = "session"
        payload["elevated_until"] = record.get("elevated_until")
        payload["auth_method"] = record.get("auth_method")
        if not self._apply_principal(payload, record):
            self.sessions.revoke(session_id)
            return None
        self._note_activity(record)
        return payload

    def _absolute_seconds(self) -> float:
        """
        Return the hard lifetime of a login, in seconds.

        Returns:
            The sign-in policy's absolute lifetime (12 hours, 8 under the ENS
            profile), capped by this console's own ceiling, which is never
            shorter than the renewal window.
        """
        ceiling = max(
            float(self.config.session_max_hours) * 3600.0,
            float(self.config.token_expiration_hours) * 3600.0,
        )
        return min(ceiling, float(self.policy().absolute_hours) * 3600.0)

    def _past_absolute_deadline(self, record: dict[str, Any]) -> bool:
        """
        Report whether a session is older than the absolute limit.

        Args:
            record: A session row.

        Returns:
            True when the login it descends from is too old to keep using.
        """
        created = float(record.get("created_at") or record["issued_at"])
        return time.time() - created >= self._absolute_seconds()

    def renew_session(self, payload: dict[str, Any]) -> IssuedSession | None:
        """
        Re-issue a session that is past half of its lifetime.

        The replacement gets a new session id and a new CSRF token, and inherits
        the original login's birth date: activity buys more idle time, never a
        longer life. The old identifier keeps working for
        :data:`SESSION_ROTATION_GRACE` seconds, for the requests already in
        flight - and each of those is answered with the *same* successor,
        re-issued, rather than a new one: a session renews once.

        Args:
            payload: A verified session payload.

        Returns:
            The refreshed session, or None when renewal is not due yet or the
            login has reached its absolute deadline.
        """
        session_id = str(payload.get("sid") or "")
        record = self.sessions.get(session_id) if session_id else None
        if record is None:
            return None

        if self._past_absolute_deadline(record):
            self.sessions.revoke(session_id)
            return None

        if record.get("rotated_to"):
            return self._reissue(str(record["rotated_to"]))

        max_age = int(self.config.token_expiration_hours * 3600)
        now_ts = time.time()
        if now_ts - record["issued_at"] < max_age * SESSION_RENEW_RATIO:
            return None

        created = float(record.get("created_at") or record["issued_at"])
        deadline = created + self._absolute_seconds()
        now = utcnow()
        expires = now + timedelta(seconds=min(float(max_age), deadline - now_ts))

        new_sid = secrets.token_hex(16)
        new_csrf = secrets.token_urlsafe(32)
        if self.sessions.rotate(session_id, new_sid, new_csrf, expires.timestamp()) is None:
            # Another request carrying the same cookie won the race to rotate
            # it; hand this one the successor that request created.
            raced = self.sessions.get(session_id)
            if raced is not None and raced.get("rotated_to"):
                return self._reissue(str(raced["rotated_to"]))
            return None

        token = self._encode(new_sid, record["client_ip"], now, expires)
        return IssuedSession(
            token=token,
            session_id=new_sid,
            csrf_token=new_csrf,
            expires_at=expires.timestamp(),
            max_age=int(expires.timestamp() - now_ts),
        )

    def _reissue(self, successor_sid: str) -> IssuedSession | None:
        """
        Hand out a successor a renewal already minted, again.

        The token is an HMAC of the sid and the CSRF token is stored, so the
        same cookie pair can be re-derived exactly; nothing new is created.

        Args:
            successor_sid: The ``rotated_to`` of a retired session.

        Returns:
            The successor, or None when it is no longer live.
        """
        successor = self.sessions.get(successor_sid)
        if successor is None:
            return None
        now = utcnow()
        expires_at = float(successor["expires_at"])
        return IssuedSession(
            token=self._encode(
                successor_sid,
                str(successor["client_ip"]),
                now,
                datetime.fromtimestamp(expires_at, tz=timezone.utc),
            ),
            session_id=successor_sid,
            csrf_token=str(successor["csrf_token"]),
            expires_at=expires_at,
            max_age=max(0, int(expires_at - time.time())),
        )

    def elevate(self, sid: str, seconds: int = ELEVATION_SECONDS) -> float:
        """
        Confirm a session for sudo mode: the destructive actions D5 names.

        Args:
            sid: Session identifier being elevated.
            seconds: How long the confirmation lasts. Defaults to the ten
                minutes the design calls for.

        Returns:
            The elevation deadline, as a UNIX timestamp.
        """
        elevated_until = _now() + seconds
        self.sessions.set_elevated(sid, elevated_until)
        return elevated_until

    def issue_ws_ticket(self, session_id: str, client_ip: str) -> tuple[str, int]:
        """
        Issue a single-use, short-lived WebSocket ticket.

        Query strings end up in access logs and proxy logs, so the value that
        travels there must be worthless seconds later and unusable twice.

        Args:
            session_id: The ``sid`` of the payload asking for the ticket: a
                cookie or Bearer session's id, :data:`MASTER_SID`, or
                ``token:<name>`` for an API token. The ticket redeems as that
                credential and nothing more; see :meth:`consume_ws_ticket`.
            client_ip: Address the ticket is issued to.

        Returns:
            The ticket value and its lifetime in seconds.
        """
        ticket = secrets.token_urlsafe(32)
        self.sessions.store_ticket(
            hashlib.sha256(ticket.encode()).hexdigest(),
            session_id,
            client_ip,
            time.time() + WS_TICKET_TTL,
        )
        return ticket, WS_TICKET_TTL

    def consume_ws_ticket(self, ticket: str, client_ip: str | None = None) -> dict[str, Any] | None:
        """
        Redeem a WebSocket ticket as the credential it was issued to.

        The credential is checked again here, not only when the ticket was
        issued: a session revoked, an API token revoked or expired, or a master
        token rotated inside the ticket's lifetime leaves nothing to redeem.
        An API token's ticket carries that token's scope, so a ticket never
        opens more than the token itself would.

        Args:
            ticket: The ticket value presented by the client.
            client_ip: Address presenting the ticket.

        Returns:
            The payload of the credential the ticket was issued to when the
            ticket and that credential are both still valid, None otherwise.
        """
        if not ticket:
            return None

        record = self.sessions.consume_ticket(hashlib.sha256(ticket.encode()).hexdigest())
        if record is None:
            return None

        if self.config.bind_session_to_ip and client_ip and record["client_ip"] != client_ip:
            return None

        sid = str(record["sid"])
        if sid == MASTER_SID:
            # generate_master_token spends these tickets, so one that is still
            # here was issued to the token in force.
            if self.policy().ens:
                return None
            return master_payload(
                record["client_ip"], self.current_master_generation(), self.master_grant()
            )
        if sid.startswith(API_TOKEN_SID_PREFIX):
            token = self.sessions.get_api_token_by_name(sid[len(API_TOKEN_SID_PREFIX) :])
            return None if token is None else self._api_token_payload(token, record["client_ip"])

        session = self.sessions.get(sid)
        if session is None or self._past_absolute_deadline(session) or self._idle_expired(session):
            return None

        payload: dict[str, Any] = {
            "type": "session",
            "sid": record["sid"],
            "ip": record["client_ip"],
            "csrf": session["csrf_token"],
            "family": session.get("family") or record["sid"],
        }
        return payload if self._apply_principal(payload, session) else None

    def issue_login_challenge(self, account_id: int, client_ip: str) -> tuple[str, int]:
        """
        Open the second step of an account's sign-in, once its password step passed.

        The value is random and only its hash is stored, so it cannot be
        forged or read back from the database; it is bound to the account and
        to the address the password came from, lasts
        :data:`LOGIN_CHALLENGE_TTL` seconds, and is spent by the sign-in it
        completes. It stands for the password in the second step, so the
        password never travels twice.

        Args:
            account_id: The account whose password was right.
            client_ip: The address it came from.

        Returns:
            The challenge and its lifetime in seconds.
        """
        challenge = secrets.token_urlsafe(32)
        self.sessions.store_login_challenge(
            hashlib.sha256(challenge.encode()).hexdigest(),
            account_id,
            client_ip,
            time.time() + LOGIN_CHALLENGE_TTL,
        )
        return challenge, LOGIN_CHALLENGE_TTL

    def login_challenge_account(self, challenge: str, client_ip: str) -> int | None:
        """
        Say which account a second step belongs to, without spending it.

        Args:
            challenge: The value presented.
            client_ip: The address presenting it.

        Returns:
            The account's id; None when the challenge is unknown, expired,
            spent or presented from another address.
        """
        if not challenge:
            return None
        record = self.sessions.get_login_challenge(hashlib.sha256(challenge.encode()).hexdigest())
        if record is None or record["client_ip"] != client_ip:
            return None
        return int(record["account_id"])

    def fail_login_challenge(self, challenge: str) -> None:
        """
        Count a wrong code against a second step; the last one it takes spends it.

        Args:
            challenge: The value presented.
        """
        self.sessions.fail_login_challenge(
            hashlib.sha256(challenge.encode()).hexdigest(), LOGIN_CHALLENGE_ATTEMPTS
        )

    def spend_login_challenge(self, challenge: str) -> bool:
        """
        Spend a second step, so it completes one sign-in and no other.

        Args:
            challenge: The value presented.

        Returns:
            True when this call spent it.
        """
        return self.sessions.spend_login_challenge(hashlib.sha256(challenge.encode()).hexdigest())

    def revoke_session(self, session_id: str) -> None:
        """
        Revoke one session.

        Args:
            session_id: The session identifier.
        """
        self.sessions.revoke(session_id)

    def list_sessions(
        self, current_sid: str | None = None, account_id: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Describe every live session without exposing a usable identifier.

        Only a truncated prefix of each session id leaves the server: enough
        to name a row for revocation, useless for forging the cookie it
        belongs to.

        Args:
            current_sid: The caller's own session id, so its row is marked.
            account_id: Only this account's sessions, when given.

        Returns:
            One dict per live session, newest activity first.
        """
        return [
            {
                "sid_prefix": str(row["sid"])[:8],
                "client_ip": str(row["client_ip"]),
                "created_at": float(row["created_at"] or row["issued_at"]),
                "last_seen": float(row.get("last_activity") or row["issued_at"]),
                "expires_at": float(row["expires_at"]),
                "is_current": bool(current_sid and row["sid"] == current_sid),
                "kind": str(row.get("kind") or SESSION_KIND_MASTER),
                "account_id": row.get("account_id"),
            }
            for row in self.sessions.list_active(account_id)
        ]

    def revoke_session_by_prefix(
        self, prefix: str, protect_sid: str | None = None, account_id: int | None = None
    ) -> str | None:
        """
        Revoke exactly one session, named by a unique prefix of its id.

        Args:
            prefix: Leading hexadecimal characters of the session id, as
                listed by :meth:`list_sessions`. At least six of them.
            protect_sid: The caller's own session id. Revoking it here is
                refused: ending the session you are inside is sign-out, and it
                has its own button that also clears the browser's cookies.

        Returns:
            The revoked session's prefix, or None when nothing matches.

        Raises:
            SecurityError: When the prefix is not hexadecimal, matches more
                than one session, or names the protected session.
        """
        candidate = prefix.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{6,32}", candidate):
            raise SecurityError(
                f"Not a session id prefix: {prefix!r}",
                details=(
                    "Prefixes are the leading characters of a session id as listed by "
                    "GET /api/auth/sessions: hexadecimal, at least six characters."
                ),
            )

        matches = self.sessions.sids_with_prefix(candidate, account_id)
        if not matches:
            return None
        if len(matches) > 1:
            raise SecurityError(
                f"The prefix {candidate!r} matches {len(matches)} sessions",
                details="Send more characters of the session id so exactly one matches.",
            )
        if protect_sid and matches[0] == protect_sid:
            raise SecurityError(
                "That is the session you are using",
                details=(
                    "Revoking the current session from here would leave the browser "
                    "holding dead cookies. Use Sign out, or POST /api/auth/logout."
                ),
            )

        self.sessions.revoke(matches[0])
        return matches[0][:8]

    def revoke_all_sessions(self) -> None:
        """Revoke every session."""
        self.sessions.revoke_all()

    def revoke_other_sessions(self, keep_sid: str, account_id: int | None = None) -> int:
        """
        Revoke every session except the one named, leaving it signed in.

        The "sign out everywhere else" button: an operator who spots a
        session they do not recognise in the list wants every *other*
        session gone without also being signed out of the tab that told
        them, which :meth:`revoke_all_sessions` cannot do.

        Args:
            keep_sid: The session id to leave untouched.
            account_id: Only this account's other sessions, when given.

        Returns:
            Number of sessions revoked.
        """
        return self.sessions.revoke_all_except(keep_sid, account_id)

    def get_active_session_count(self) -> int:
        """
        Count usable sessions.

        Returns:
            Number of live sessions.
        """
        return self.sessions.active_count()

    def purge_expired_sessions(self) -> int:
        """
        Delete expired and revoked sessions.

        Returns:
            Number of rows removed.
        """
        return self.sessions.purge_expired()

    def rotate_secrets(self) -> str:
        """
        Rotate the signing key and the master token, killing every session.

        Returns:
            The new master token.

        Raises:
            SecurityError: When the new secrets cannot be persisted.
        """
        self._secret_key = secrets.token_hex(SECRET_KEY_LENGTH)
        write_private_file(self.config.secret_file, self._secret_key)
        self._secret_stamp = _file_stamp(self.config.secret_file)
        self.revoke_all_sessions()
        self.purge_expired_sessions()
        return self.generate_master_token()


def _parse_networks(entries: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """
    Parse IP or CIDR strings, ignoring malformed entries.

    Args:
        entries: Addresses or networks from the configuration.

    Returns:
        The parsed networks.
    """
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            logger.warning("Ignoring invalid IP or CIDR in configuration: %r", entry)
    return networks


def ip_matches(candidate: str, entries: list[str]) -> bool:
    """
    Check an address against a list of addresses or CIDRs.

    Args:
        candidate: The address to test.
        entries: Allowed addresses or networks.

    Returns:
        True when the address falls inside one of the entries.
    """
    if not entries:
        return False
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return any(address in network for network in _parse_networks(entries))


def _as_ip_address(value: str) -> str | None:
    """
    Return the value when it is a bare IP address.

    Args:
        value: A candidate address, possibly with surrounding whitespace or
            brackets around an IPv6 literal.

    Returns:
        The normalised address, or None when it is not an IP address at all.
    """
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def get_client_ip(connection: HTTPConnection, config: SecurityConfig | None = None) -> str:
    """
    Determine the client address that security decisions are keyed on.

    The policy, in one sentence: **the peer address is the truth unless the peer
    is a proxy we deployed, and even then only a parseable IP address is
    believed.** Two rules follow from it.

    - Forwarding headers are read only when the direct peer matches
      ``trusted_proxies``, which is empty by default. Otherwise any client could
      pick its own identity and rotate out of a lockout or a rate limit.
    - A hop that is not a valid IP address is discarded rather than used. A
      trusted proxy can be tricked into appending client-supplied garbage, and a
      free-form string as the limiter's key is the same unbounded-rotation bug
      one layer down.

    Args:
        connection: The incoming HTTP request or WebSocket handshake.
        config: Configuration to use; the installed one by default.

    Returns:
        The client's IP address, the peer address when no header applies, or
        ``"unknown"`` when there is no peer at all.
    """
    config = config or get_security_config()
    peer = connection.client.host if connection.client else ""

    if peer and config.trusted_proxies and ip_matches(peer, config.trusted_proxies):
        forwarded_for = connection.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Walk right to left: the rightmost entry that is not one of our own
            # proxies is the first address our infrastructure actually observed.
            for raw_hop in reversed(forwarded_for.split(",")):
                hop = _as_ip_address(raw_hop)
                if hop is None:
                    # Anything unparseable ends the walk: entries to its left
                    # were appended before it and are just as untrustworthy.
                    break
                if not ip_matches(hop, config.trusted_proxies):
                    return hop
        real_ip = connection.headers.get("X-Real-IP")
        if real_ip:
            parsed = _as_ip_address(real_ip)
            if parsed is not None:
                return parsed

    return peer or "unknown"


def is_secure_request(connection: HTTPConnection, config: SecurityConfig | None = None) -> bool:
    """
    Report whether the request reached the panel over TLS.

    Args:
        connection: The incoming request or WebSocket handshake.
        config: Configuration to use; the installed one by default.

    Returns:
        True when the connection is TLS, directly or through a trusted proxy
        that declared it with ``X-Forwarded-Proto``.
    """
    config = config or get_security_config()
    if connection.url.scheme in ("https", "wss"):
        return True

    peer = connection.client.host if connection.client else ""
    if peer and config.trusted_proxies and ip_matches(peer, config.trusted_proxies):
        return connection.headers.get("X-Forwarded-Proto", "").strip().lower() in ("https", "wss")

    return False


def _origin_host(origin: str) -> str | None:
    """
    Extract the host of an Origin header.

    Args:
        origin: The header value.

    Returns:
        The lowercase host with its port, or None when the value is opaque
        (``null``) or not an absolute origin.
    """
    value = origin.strip().lower()
    if not value or value == "null":
        return None
    _scheme, separator, remainder = value.partition("://")
    if not separator or not remainder:
        return None
    return remainder.split("/", 1)[0] or None


def is_allowed_origin(connection: HTTPConnection, config: SecurityConfig | None = None) -> bool:
    """
    Check the Origin of a handshake against the hosts allowed to open one.

    A ``SameSite=Strict`` cookie is still sent by a sibling subdomain, because
    same-site is not same-origin. Without this check an XSS anywhere under the
    parent domain could open ``/ws/logs/{domain}`` and read the root journal, a
    cross-site WebSocket hijack.

    Args:
        connection: The incoming handshake.
        config: Configuration to use; the installed one by default.

    Returns:
        True when the request carries no Origin (a non-browser client, which
        cannot be tricked into ambient authority), when it matches the Host the
        request was addressed to, or when it is explicitly configured.
    """
    config = config or get_security_config()
    origin = connection.headers.get("origin")
    if not origin:
        return True

    origin_host = _origin_host(origin)
    if origin_host is None:
        return False

    host_header = connection.headers.get("host", "").strip().lower()
    if host_header and origin_host == host_header:
        return True

    allowed = {value.strip().lower() for value in config.cors_origins}
    if origin.strip().lower() in allowed:
        return True
    return any(_origin_host(value) == origin_host for value in allowed)


def is_safe_path(path: str) -> bool:
    """
    Check that a path stays inside its base directory.

    Args:
        path: The path to check.

    Returns:
        True when the path is relative and contains no traversal.
    """
    normalized = os.path.normpath(path)
    if ".." in normalized.split(os.sep):
        return False
    return not normalized.startswith("/")


def sanitize_input(value: str, max_length: int = 1000) -> str:
    """
    Trim and de-fang a user supplied string.

    Args:
        value: The input value.
        max_length: Maximum allowed length.

    Returns:
        The sanitized value.
    """
    if not value:
        return ""
    return value[:max_length].replace("\x00", "")


def _unauthorized(detail: str) -> HTTPException:
    """
    Build a 401 response.

    Args:
        detail: Message for the client.

    Returns:
        The exception to raise.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def bearer_token(connection: HTTPConnection) -> str | None:
    """
    Extract a Bearer token from the Authorization header.

    Args:
        connection: The incoming request or handshake.

    Returns:
        The token, or None when the header is absent or not a Bearer header.
    """
    header = connection.headers.get("Authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


def subprotocol_token(connection: HTTPConnection) -> str | None:
    """
    Extract a session token from the requested WebSocket subprotocols.

    Args:
        connection: The pending handshake.

    Returns:
        The token, or None when no subprotocol carries one.
    """
    header = connection.headers.get("sec-websocket-protocol", "")
    for entry in (part.strip() for part in header.split(",")):
        if entry.startswith(WS_TOKEN_PREFIX):
            return entry[len(WS_TOKEN_PREFIX) :] or None
    return None


#: Prefix of the lockout key a retired fleet token's failures are counted
#: under, instead of the address it arrived from (see :func:`failure_key`).
FLEET_LOCKOUT_PREFIX = "fleet:"


def failure_key(credential: str | None, client_ip: str) -> str:
    """
    Choose what a failed credential is counted against in the lockout.

    The address, always, except for a revoked or expired fleet token: the
    central that still holds one arrives from loopback, where the operator's
    SSH-tunnelled console arrives too, and counting it there would lock the
    operator out for the lockout's length. Its failures are counted under
    ``fleet:<token name>`` instead, which locks nothing anybody uses.

    Args:
        credential: The credential that failed, if one was presented.
        client_ip: The address it came from.

    Returns:
        The lockout key.
    """
    manager = get_global_token_manager()
    if credential and manager is not None:
        name = manager.retired_fleet_token_name(credential)
        if name is not None:
            return f"{FLEET_LOCKOUT_PREFIX}{name}"
    return client_ip


def shared_address(client_ip: str, config: SecurityConfig | None = None) -> bool:
    """
    Report whether an address stands for many people rather than one.

    Loopback is where every operator reaching the console over ``ssh -L``
    arrives from, and a trusted proxy's own address is what a request that
    passed it without a usable forwarding header resolves to
    (:func:`get_client_ip`). Neither says who is on the other end.

    Args:
        client_ip: The resolved client address.
        config: Configuration to use; the installed one by default.

    Returns:
        True for a loopback address or one of ``trusted_proxies``.
    """
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return True
    config = config or get_security_config()
    return ip_matches(str(address), config.trusted_proxies)


def sign_in_lockout_key(
    client_ip: str, identifier: str | None, config: SecurityConfig | None = None
) -> str:
    """
    Choose what a sign-in attempt is counted and refused under.

    The address, for an address that is one client's. Behind an address many
    people share (:func:`shared_address`), the address and the name the
    attempt named: five typos by one operator on an SSH tunnel would
    otherwise lock every operator on it out at once. What still bounds a
    guesser there is the per-account lockout, which counts every wrong
    password or code on the account whatever the address
    (:meth:`~noust.core.accounts.AccountManager.record_failure`).

    Args:
        client_ip: The resolved client address.
        identifier: The username or e-mail the attempt named; None for the
            master token, which is counted under a name of its own.
        config: Configuration to use; the installed one by default.

    Returns:
        The lockout key: the address itself, or a ``sign-in:`` key.
    """
    if not shared_address(client_ip, config):
        return client_ip
    if identifier is None:
        return f"{SIGN_IN_LOCKOUT_PREFIX}{client_ip}:master"
    # Cut and folded like the names it stands for, so a key is bounded and one
    # person's typing in another case is still one person.
    name = "".join(char for char in identifier.strip().lower() if char.isprintable())[:254]
    return f"{SIGN_IN_LOCKOUT_PREFIX}{client_ip}:account:{name}"


def audit_event(
    event: str,
    outcome: str,
    *,
    client_ip: str,
    session: Mapping[str, Any] | None = None,
    target: str | None = None,
    detail: str | None = None,
) -> None:
    """
    Record one event of the console's identity layer in the audit trail.

    The one door the sign-in, account and token endpoints record through, so
    the events keep one shape: a name from the audit catalog
    (``auth.login``, ``user.create``...), the actor as :func:`actor_label`
    names it, the address, the target and one sentence. Never a credential.

    Args:
        event: Catalog name of what happened.
        outcome: ``success``, ``failure``, ``denied`` or ``warning``.
        client_ip: Where the request came from.
        session: The authenticated payload, or None for an anonymous caller.
        target: What it was done to, such as ``account:maria``.
        detail: One sentence of context.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action=event,
            result=outcome,
            client_ip=client_ip,
            actor=actor_label(session) if session else "anonymous",
            resource=target,
            detail=detail,
        )


def record_auth_failure(
    client_ip: str, resource: str, source: str, *, lockout_key: str | None = None
) -> None:
    """
    Count and audit one rejected credential, whatever channel it arrived on.

    This is the only place that increments the lockout counter for a bad
    credential. A cookie, a ``Bearer`` header on any endpoint and a WebSocket
    handshake all land here, so an attacker cannot reset the count by changing
    endpoint or by moving from HTTP to ``/ws``.

    Args:
        client_ip: Address the credential came from.
        resource: Path that was being reached.
        source: Channel the credential arrived on, for the audit record.
        lockout_key: What to count the failure against, when it is not the
            address (:func:`failure_key`).
    """
    key = lockout_key or client_ip
    protection = get_brute_force_protection()
    if protection is not None:
        protection.record_failure(key)

    detail = f"invalid credential presented via {source}"
    if key.startswith(FLEET_LOCKOUT_PREFIX):
        detail = (
            f"revoked or expired fleet token {key.removeprefix(FLEET_LOCKOUT_PREFIX)} "
            f"presented via {source}; counted under the token, not the address"
        )
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.credential",
            result="denied",
            client_ip=client_ip,
            resource=resource,
            detail=detail,
        )


def is_elevated(payload: dict[str, Any]) -> bool:
    """
    Report whether a session payload is currently inside its sudo-mode window.

    Args:
        payload: A verified session payload, as :func:`require_auth` builds
            it. Only a cookie session ever carries a meaningful
            ``elevated_until``; see :func:`noust.web.api.deps.ensure_elevated`
            for who is asked at all.

    Returns:
        True while ``POST /api/auth/elevate`` was called within the last
        :data:`ELEVATION_SECONDS`.
    """
    elevated_until = payload.get("elevated_until")
    if elevated_until is None:
        return False
    return float(elevated_until) > _now()


def scope_satisfies(granted: str, required: str) -> bool:
    """
    Report whether a granted scope covers a required one.

    Args:
        granted: Scope carried by the credential.
        required: Scope the operation demands.

    Returns:
        True when the grant is at or above the requirement. An unknown scope
        on either side fails closed, and so does a raw ``fleet`` scope: an
        admitted fleet payload carries the scope :func:`admit_fleet` gave it,
        so one still saying ``fleet`` skipped admission and is worth nothing.
    """
    wanted = SCOPE_RANK.get(required)
    if wanted is None or granted == FLEET_SCOPE:
        return False
    return SCOPE_RANK.get(granted, -1) >= wanted


def sees_command_lines(payload: Mapping[str, Any]) -> bool:
    """
    Decide whether a credential may read other processes' command lines.

    A process's argv routinely carries a secret it was started with - a
    database password or an API token passed as a flag - and every process
    listing the panel serves would otherwise hand it to a ``read`` token given
    to a dashboard. The policy, stated once for every listing: ``admin`` sees
    the command line, anything less sees the process name.

    Args:
        payload: The authenticated payload.

    Returns:
        True for a credential holding ``secrets.reveal``: an admin, the
        master token, and an ``admin`` token of 3.0.
    """
    return has_permission(payload, Permission.SECRETS_REVEAL)


def actor_label(session: Mapping[str, Any]) -> str:
    """
    A short, non-secret label naming who is behind a session payload.

    The one way an action records who did it: every audit record, every
    audit log line and every job's actor go through here, so the Activity
    page can filter the audit log by the same actor a job shows. Reading the
    payload by hand is how three routers came to log ``session=unknown`` for
    every call - they read a ``session_id`` key that payloads never had.

    An API token's payload already carries a human name (``token:<name>``,
    set by :meth:`TokenManager.verify_api_token`); the master token's is the
    literal ``master``; a cookie session's id is an opaque, non-secret
    identifier - the credential is the signed cookie value, not the id alone -
    shortened here to keep a jobs list readable. Twelve characters still start
    with the prefix ``GET /api/auth/sessions`` lists, so a line can be traced
    to the session that wrote it.

    Args:
        session: The authenticated session payload, as :func:`require_auth`
            builds it.

    Returns:
        The username of an account's session, ``"master"``,
        ``"token:<name>"``, the first 12 characters of a master token
        session's id, or ``"<fleet token name> on behalf of <actor>"`` for a
        central acting for one of its operators.
    """
    on_behalf_of = session.get("on_behalf_of")
    if session.get("fleet") and on_behalf_of:
        return f"{session.get('token_name') or 'fleet'} on behalf of {on_behalf_of}"
    if session.get("account_id") is not None and session.get("username"):
        # A person, by the name they sign in with: the audit trail names who
        # acted, not which of their sessions (ENS op.exp.8, art. 24.3).
        return str(session["username"])
    sid = str(session.get("sid") or "unknown")
    if sid in (MASTER_SID, "unknown") or sid.startswith(API_TOKEN_SID_PREFIX):
        return sid
    return sid[:12]


class CredentialRefused(HTTPException):
    """
    A real credential presented where, or how, it is not accepted.

    Not a guess, so it is audited but never counted by the lockout: a fleet
    token from outside its tunnel, an API token from outside its networks or
    whose owner was disabled, the master token as a Bearer under the ENS
    profile. Raised where the credential is resolved, so no endpoint can
    accept one. It is an ``HTTPException`` because the API's error boundary
    already answers those in the one contract, and a dict ``detail`` carries
    its own ``error`` code there.

    Attributes:
        reason: The sentence answered, for the audit record.
        action: The audit action it is recorded under.
    """

    def __init__(
        self, status_code: int, error: str, detail: str, hint: str, *, action: str = "auth.fleet"
    ) -> None:
        """
        Args:
            status_code: 401 for a credential from the wrong place, 400 for a
                malformed header, 403 for a refused operation.
            error: Machine-readable code for the error contract.
            detail: What was refused.
            hint: What to do instead.
            action: The audit action.
        """
        super().__init__(
            status_code=status_code,
            detail={"error": error, "detail": detail, "hint": hint, "fields": None},
            headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
        )
        self.reason = detail
        self.action = action


class FleetCredentialRefused(CredentialRefused):
    """A fleet token presented where, or how, it is not accepted."""


def is_fleet(payload: Mapping[str, Any]) -> bool:
    """
    Report whether a payload was authenticated by a central's fleet token.

    Args:
        payload: A verified payload.

    Returns:
        True for an admitted fleet payload, or a raw one that still says so.
    """
    return bool(payload.get("fleet")) or payload.get("scope") == FLEET_SCOPE


def fleet_origin_refusal(connection: HTTPConnection | None) -> str | None:
    """
    Say why a connection may not carry a fleet token, if it may not.

    The peer is the TCP peer, not :func:`get_client_ip`'s answer: a trusted
    proxy's ``X-Forwarded-For`` names somebody else by design. And loopback
    is not enough on its own, because a reverse proxy on this machine is
    loopback too; the headers such a proxy adds are what tell it apart from
    the SSH tunnel, which adds none.

    Args:
        connection: The request or handshake, or None when the caller has
            none to offer - which fails closed.

    Returns:
        None when the token may be used, otherwise the sentence to refuse with.
    """
    if connection is None:
        return "A fleet token is only accepted on a request this server can see the peer of."
    peer = connection.client.host if connection.client else ""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        return (
            "A fleet token is only accepted from this server's own loopback, "
            "where the central's SSH tunnel arrives."
        )
    forwarded = [name for name in FORWARDING_HEADERS if name in connection.headers]
    if forwarded:
        return (
            "A fleet token is not accepted through a reverse proxy "
            f"(the request carries {', '.join(forwarded)}); the central reaches this "
            "server through its SSH tunnel only."
        )
    return None


def admit_fleet(payload: dict[str, Any], connection: HTTPConnection | None) -> dict[str, Any]:
    """
    Turn a fleet token's raw payload into what the request may do.

    The one place a fleet payload is admitted: the peer must be the tunnel,
    ``X-Noust-Actor`` names the operator on the central, the token is
    narrowed to ``X-Noust-Actor-Scope``, and ``X-Noust-Elevated`` records
    whether the central confirmed that operator's sudo mode. The raw
    ``fleet`` scope is replaced, so a payload that skipped this satisfies no
    scope at all (see :func:`scope_satisfies`). Missing or invalid,
    ``X-Noust-Actor-Scope`` grants ``read``, never ``admin``: an older
    central, or a caller on this central with no human actor to narrow to
    (a status poll), must ask for admin explicitly rather than receive it by
    omission.

    Args:
        payload: The raw payload of a fleet token. Modified in place.
        connection: The request or handshake that presented it.

    Returns:
        The same payload, admitted.

    Raises:
        FleetCredentialRefused: 401 when the peer is not the tunnel, 400 when
            a fleet header is malformed.
    """
    refusal = fleet_origin_refusal(connection)
    if refusal is not None or connection is None:
        raise FleetCredentialRefused(
            401,
            "fleet_origin",
            refusal or "A fleet token needs a connection.",
            "Reach this server through the central, or use a token of your own.",
        )

    actor = connection.headers.get(FLEET_ACTOR_HEADER)
    if actor is not None and not FLEET_ACTOR_PATTERN.fullmatch(actor):
        raise FleetCredentialRefused(
            400,
            "validation_error",
            f"{FLEET_ACTOR_HEADER} must be 1 to 64 letters, digits or ._:@+-, "
            "starting with a letter or digit.",
            "The central sends its operator's label; check the central's version.",
        )

    # Fail closed: absent or malformed, the grant is the lowest scope, not
    # the highest. An older central omitting the header, or a request this
    # central makes with no operator behind it, gets read, never admin by
    # accident.
    granted = "read"
    actor_scope = connection.headers.get(FLEET_ACTOR_SCOPE_HEADER)
    if actor_scope is not None:
        if actor_scope not in SCOPE_RANK:
            raise FleetCredentialRefused(
                400,
                "validation_error",
                f"{FLEET_ACTOR_SCOPE_HEADER} must be one of: {', '.join(API_TOKEN_SCOPES)}.",
                "The central sends its operator's scope; check the central's version.",
            )
        granted = actor_scope

    # A 3.1 central names its operator's role; this node grants what its own
    # table gives that role, never what the central says it may do.
    role: str | None = None
    base = permissions_for_scope(granted)
    actor_role = connection.headers.get(FLEET_ACTOR_ROLE_HEADER)
    if actor_role is not None:
        if not FLEET_ROLE_PATTERN.fullmatch(actor_role):
            raise FleetCredentialRefused(
                400,
                "validation_error",
                f"{FLEET_ACTOR_ROLE_HEADER} must be a role name: lower-case letters, '-' or '_'.",
                "The central sends its operator's role; check the central's version.",
            )
        role = actor_role if actor_role in ROLE_PERMISSIONS else "viewer"
        base = permissions_for_role(role)
    permits = fleet_ceiling()
    permissions = frozenset(permission for permission in base if permits(permission))

    payload["fleet"] = True
    payload["role"] = role
    payload["permissions"] = permissions
    payload["scope"] = legacy_scope(permissions)
    payload["on_behalf_of"] = actor
    payload["elevation_attested"] = connection.headers.get(FLEET_ELEVATED_HEADER) == "1"
    return payload


def fleet_ceiling() -> Callable[[str], bool]:
    """
    The most this node lets any central do, as a test of one permission.

    ``noust fleet authorize --access read|deploy|admin`` (and ``noust fleet
    access``) set it on the node; :mod:`noust.fleet.policy` reads it. A fleet
    request is granted its role's permissions intersected with this.

    Returns:
        A function saying whether the ceiling permits a permission.
    """
    policy = importlib.import_module("noust.fleet.policy")
    current_access = getattr(policy, "current_access", None)
    permits = getattr(policy, "permits", None)
    if current_access is None or permits is None:
        # INTEGRATION POINT (B5a): noust.fleet.policy.current_access() and
        # permits() are being added with the per-node ceiling. Until they
        # exist the ceiling is the one 3.0 had - admin, everything - so a
        # node upgraded ahead of its ceiling keeps working as before.
        return lambda permission: True
    access = current_access()
    return lambda permission: bool(permits(access, permission))


def _under(path: str, prefix: str) -> bool:
    """
    Report whether a path is a prefix or below it, segment-wise.

    Args:
        path: The request path.
        prefix: A path prefix without a trailing slash.

    Returns:
        True for ``prefix`` itself and anything under ``prefix/``.
    """
    return path == prefix or path.startswith(prefix + "/")


def fleet_refusal(method: str, path: str, config_key: str | None = None) -> str | None:
    """
    The operations a fleet token may never perform, stated once.

    A central manages a node's applications, services, databases and
    backups with its operator's authority. It does not manage the node's
    own credentials - tokens, two-factor authentication, sessions, sudo mode,
    WebSocket tickets - nor who may reach the node's console, nor fleet
    enrollment: those stay with whoever holds root on the node, so a
    compromised central cannot lock that operator out, widen the node's
    exposure, or mint itself a credential that outlives revocation.

    Args:
        method: The HTTP method; ``GET`` for a WebSocket handshake.
        path: The request path.
        config_key: The dotted key a ``PATCH /api/config`` body addresses, when
            the request is one; ignored otherwise.

    Returns:
        None when the operation is allowed, otherwise the sentence to refuse with.
    """
    verb = method.upper()
    if path in FLEET_AUTH_PATHS:
        return None
    for prefix in FLEET_REFUSED_PREFIXES:
        if _under(path, prefix):
            return (
                f"A central cannot reach {path} on a node: credentials and fleet "
                "enrollment belong to the node's own operator."
            )
    if (verb, path) in FLEET_REFUSED_WRITES:
        return (
            f"A central cannot {verb} {path} on a node: it carries the node's console "
            "security settings."
        )
    if verb == "PATCH" and path == "/api/config" and config_key is not None:
        section = config_key.strip().split(".", 1)[0]
        if section in FLEET_PROTECTED_CONFIG_SECTIONS:
            return (
                f"A central cannot change '{config_key}' on a node: the '{section}' "
                "settings belong to the node's own operator."
            )
    return None


def _refuse_fleet_operation(
    connection: HTTPConnection, payload: Mapping[str, Any], reason: str
) -> None:
    """
    Audit and raise a refused fleet operation.

    Args:
        connection: The request or handshake.
        payload: The admitted fleet payload.
        reason: The sentence from :func:`fleet_refusal`.

    Raises:
        FleetCredentialRefused: Always, as a 403.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.fleet",
            result="denied",
            client_ip=get_client_ip(connection),
            actor=actor_label(payload),
            resource=str(connection.scope.get("path", "")),
            detail=reason,
        )
    raise FleetCredentialRefused(
        403,
        "forbidden",
        reason,
        "Run it on the node itself, as its operator.",
    )


async def ensure_fleet_allowed(request: Request, payload: Mapping[str, Any]) -> None:
    """
    Refuse a fleet request :func:`fleet_refusal` names, at ``require_auth``.

    Args:
        request: The incoming request.
        payload: The verified payload.

    Raises:
        FleetCredentialRefused: 403 when the operation is one a fleet token
            may not perform.
    """
    if not is_fleet(payload):
        return
    method = request.method.upper()
    path = request.url.path
    config_key = (
        await patched_config_key(request) if (method, path) == ("PATCH", "/api/config") else None
    )
    reason = fleet_refusal(method, path, config_key)
    if reason is not None:
        _refuse_fleet_operation(request, payload, reason)


def ensure_fleet_handshake_allowed(connection: HTTPConnection, payload: Mapping[str, Any]) -> None:
    """
    Refuse a fleet WebSocket handshake :func:`fleet_refusal` names.

    Args:
        connection: The pending handshake.
        payload: The verified payload.

    Raises:
        FleetCredentialRefused: 403 when the stream is one a fleet token may
            not open.
    """
    if not is_fleet(payload):
        return
    reason = fleet_refusal("GET", str(connection.scope.get("path", "")))
    if reason is not None:
        _refuse_fleet_operation(connection, payload, reason)


def required_scope(method: str, path: str) -> str:
    """
    The 3.0 scope policy: what a request needed before routes had permissions.

    Reads need ``read``. The mutations that move an existing application - an
    update, a rollback, activating a release - need ``deploy``. Every other
    mutation, creating an application included, needs ``admin``. Since 3.1
    every route of this server declares a permission instead
    (:mod:`noust.web.permissions`); this rule is kept for the one case no map
    can answer - a path of a node, reached through the proxy, that this
    server does not know - so a newer node is reached no more loosely than
    3.0 reached it.

    Args:
        method: The HTTP method.
        path: The request path. A node-proxied path
            (``/api/nodes/{node}/api/...``) is judged by what follows the
            node, exactly as it would be judged locally.

    Returns:
        The minimum scope.
    """
    proxied = _NODE_PROXY_PATH.match(path)
    if proxied is not None:
        path = proxied.group(1)
    verb = method.upper()
    if verb in SAFE_METHODS:
        return "read"
    if verb == "POST" and (
        path in DEPLOY_SCOPE_PATHS or any(p.match(path) for p in DEPLOY_SCOPE_PATTERNS)
    ):
        return "deploy"
    return "admin"


def ensure_scope(request: Request, payload: dict[str, Any], required: str) -> None:
    """
    Refuse a request whose credential does not carry the scope it needs.

    For the handlers whose need depends on what they are asked (an unmasked
    ``.env``, the console's own journal): the route's permission is enforced
    before they run, by :func:`require_auth`. A payload's ``scope`` is the 3.0
    scope its permissions amount to (:func:`noust.web.permissions.roles.legacy_scope`).

    Args:
        request: The incoming request.
        payload: The verified session payload; its ``scope`` was set where the
            credential was resolved. A payload without one fails closed as
            ``read``.
        required: The scope the operation demands.

    Raises:
        HTTPException: 403 when the scope is insufficient. The refusal is
            audited with the token's name, never the token.
    """
    granted = str(payload.get("scope") or "read")
    if scope_satisfies(granted, required):
        return

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="auth.scope",
            result="denied",
            client_ip=get_client_ip(request),
            actor=actor_label(payload),
            resource=request.url.path,
            detail=f"scope '{granted}' below required '{required}'",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"This credential's scope is '{granted}', and {request.method} "
            f"{request.url.path} requires '{required}'. Issue a token with the "
            "right scope from Settings, or POST /api/auth/tokens."
        ),
    )


def master_payload(
    client_ip: str | None, generation: str | None = None, grant: str = GRANT_COMPAT
) -> dict[str, Any]:
    """
    The payload the master token authenticates, wherever it is presented.

    Args:
        client_ip: Address presenting it.
        generation: Which issue of the master token it was, from
            :meth:`TokenManager.master_token_generation`, so a stream can
            tell later whether it has been rotated since.
        grant: How it holds the console (:meth:`TokenManager.master_grant`).

    Returns:
        A payload named :data:`MASTER_SID` with the grant's permissions.
    """
    permissions = GRANT_PERMISSIONS[grant]
    return {
        "type": "master",
        "sid": MASTER_SID,
        "ip": client_ip,
        "scope": legacy_scope(permissions),
        "generation": generation,
        "grant": grant,
        "permissions": permissions,
        # The one credential 3.0 exempted from sudo mode as a Bearer, kept
        # for compatibility; the ENS profile refuses the Bearer outright.
        "elevation_exempt": True,
    }


def credential_is_current(payload: Mapping[str, Any]) -> bool:
    """
    Re-check a credential verified earlier; see :meth:`TokenManager.credential_is_current`.

    Args:
        payload: The payload a stream was opened with.

    Returns:
        True while it would still be accepted. False when the server was
        never initialised: a stream nobody can vouch for is not current.
    """
    manager = get_global_token_manager()
    if manager is None:
        return False
    return manager.credential_is_current(payload)


def credential_key(payload: Mapping[str, Any]) -> str:
    """
    Name the credential behind a payload for per-credential accounting.

    Args:
        payload: A verified payload.

    Returns:
        ``master``, ``token:<name>``, or ``session:<family>`` - the sign-in,
        not the sid, so the connections a session opened before and after a
        renewal count against one budget. A fleet token is keyed per actor
        too: one token carries every operator of a central, and eight log
        streams shared by all of them would be one busy operator's worth.
    """
    if payload.get("type") == "session":
        return f"session:{payload.get('family') or payload.get('sid')}"
    if payload.get("fleet") and payload.get("on_behalf_of"):
        return f"{payload.get('sid') or 'unknown'}#{payload['on_behalf_of']}"
    return str(payload.get("sid") or "unknown")


#: Paths whose endpoints verify a credential the request carries. Anywhere
#: else - ``/health``, the forge webhooks - nothing ever checks one, so the
#: rate limiter does not either: a credential it looked at there would land a
#: valid guess in the roomy budget, say so in ``X-RateLimit-Limit``, and never
#: count a wrong one.
CREDENTIAL_PATH_PREFIXES = ("/api", "/events", "/ws")


def carries_credentials(path: str) -> bool:
    """
    Report whether a request path is one whose endpoints verify credentials.

    Args:
        path: The request path.

    Returns:
        True for a path equal to or below one of
        :data:`CREDENTIAL_PATH_PREFIXES`, matched by whole segment.
    """
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in CREDENTIAL_PATH_PREFIXES
    )


@dataclass
class CredentialLedger:
    """
    The wrong credentials one request presented, and whether each was counted.

    The rate limiter checks a credential before any endpoint does, and an
    endpoint may never check it (a public route, a 404). Without a ledger the
    limiter had two bad choices: count the failure itself, so every endpoint
    that also counts it counted it twice, or count nothing, and be an oracle
    wherever no endpoint looked. So the limiter notes each wrong credential
    here, :func:`verify_credential` and :func:`authenticate_connection` mark
    the ones they count, and :func:`settle_credential_failures` counts the
    rest once the request is over.

    Attributes:
        client_ip: The address the request came from.
        resource: The path it reached, for the audit record.
        pending: Fingerprint of each wrong credential not yet counted, mapped
            to the channel it arrived on and the lockout key it counts under.
    """

    client_ip: str
    resource: str
    pending: dict[str, tuple[str, str]] = field(default_factory=dict)


_credential_ledger: ContextVar[CredentialLedger | None] = ContextVar(
    "wasm_credential_ledger", default=None
)


def _fingerprint(credential: str) -> str:
    """
    Name a credential without keeping it.

    Args:
        credential: The credential.

    Returns:
        Its SHA-256, hexadecimal.
    """
    return hashlib.sha256(credential.encode("utf-8", "surrogateescape")).hexdigest()


def open_credential_ledger(client_ip: str, resource: str) -> Token[CredentialLedger | None]:
    """
    Start accounting for the wrong credentials of one request.

    Args:
        client_ip: The resolved client address.
        resource: The request path.

    Returns:
        The token :func:`settle_credential_failures` closes the ledger with.
    """
    return _credential_ledger.set(CredentialLedger(client_ip, resource))


def settle_credential_failures(token: Token[CredentialLedger | None]) -> None:
    """
    Count every wrong credential the request presented that nothing counted.

    Args:
        token: What :func:`open_credential_ledger` returned for this request.
    """
    ledger = _credential_ledger.get()
    _credential_ledger.reset(token)
    if ledger is None:
        return
    for source, key in ledger.pending.values():
        record_auth_failure(ledger.client_ip, ledger.resource, source, lockout_key=key)


def _note_wrong_credential(credential: str, source: str) -> None:
    """
    Note a wrong credential the rate limiter found, for counting later.

    Args:
        credential: The credential.
        source: Channel it arrived on.
    """
    ledger = _credential_ledger.get()
    if ledger is not None:
        fingerprint = _fingerprint(credential)
        if fingerprint not in ledger.pending:
            ledger.pending[fingerprint] = (source, failure_key(credential, ledger.client_ip))


def _mark_counted(*credentials: str | None) -> None:
    """
    Take credentials off the ledger because their failure has been counted.

    Args:
        *credentials: The credentials whose failure was just recorded.
    """
    ledger = _credential_ledger.get()
    if ledger is None:
        return
    for credential in credentials:
        if credential:
            ledger.pending.pop(_fingerprint(credential), None)


def _is_guess(credential: str) -> bool:
    """
    Report whether a credential that did not verify counts as a guess.

    Args:
        credential: A credential :func:`check_credential` refused.

    Returns:
        False for a session token this server signed: it can be expired,
        revoked or presented from the wrong address, but producing one takes
        the signing key, and counting it would lock out the operator whose
        browser simply outlived its session.
    """
    manager = get_global_token_manager()
    return manager is None or not manager.signed_session_token(credential)


def rate_limit_identity(connection: HTTPConnection, client_ip: str) -> str | None:
    """
    Name the valid credential a request carries, for the rate limiter.

    Checks the channels a request can carry a credential on - the session
    cookie, the ``wasm.token.`` subprotocol of a WebSocket handshake, the
    ``Authorization`` header - with :func:`check_credential`. A wrong one is
    noted on the request's :class:`CredentialLedger`, and counted against the
    lockout once: by the endpoint that refuses it, or when the request ends if
    no endpoint looked at it. A single-use WebSocket ticket is not looked at,
    because checking it would spend it.

    Args:
        connection: The incoming request or handshake.
        client_ip: The resolved client address, which a session is bound to.

    Returns:
        :func:`credential_key` of the first valid credential, or None when the
        request carries none, in which case it is counted by address.
    """
    candidates = [("cookie", connection.cookies.get(SESSION_COOKIE_NAME))]
    if connection.scope["type"] == "websocket":
        candidates.append(("websocket", subprotocol_token(connection)))
    candidates.append(("bearer", bearer_token(connection)))
    for source, credential in candidates:
        if not credential:
            continue
        try:
            payload = check_credential(credential, client_ip, connection)
        except CredentialRefused:
            # A real token from the wrong place is not a guess; the endpoint
            # refuses and audits it, and the request is counted by address.
            return None
        if payload is not None:
            return credential_key(payload)
        if _is_guess(credential):
            _note_wrong_credential(credential, source)
    return None


def check_credential(
    credential: str, client_ip: str, connection: HTTPConnection | None = None
) -> dict[str, Any] | None:
    """
    Verify one credential without recording anything.

    Args:
        credential: A session token, an API token or the master token.
        client_ip: Address presenting it.
        connection: The request or handshake that carries it. A fleet token
            is admitted against it (:func:`admit_fleet`); without one a fleet
            token is refused.

    Returns:
        The session payload, or None when the credential is not valid.

    Raises:
        CredentialRefused: When the credential is a fleet token that the
            connection may not carry, an API token refused on this request,
            or the master token under the ENS profile.
    """
    manager = get_global_token_manager()
    if manager is None or not credential:
        return None

    # The prefix routes to the token table and nowhere else, so an expired or
    # revoked API token cannot fall through to a slower master-token check.
    if credential.startswith(API_TOKEN_PREFIXES):
        token_payload = manager.verify_api_token(credential, client_ip)
        if token_payload is not None and is_fleet(token_payload):
            return admit_fleet(token_payload, connection)
        return token_payload

    payload = manager.verify_session_token(credential, client_ip)
    if payload is not None:
        return payload

    generation = manager.master_token_generation(credential)
    if generation is not None:
        grant = manager.master_grant()
        if grant == GRANT_RECOVERY:
            # ENS: the master token recovers access through the sign-in
            # page, as a session with its own second factor and audit trail;
            # as a standing Bearer it is a way around both (finding H1).
            raise CredentialRefused(
                401,
                "master_bearer_disabled",
                "The master token is not accepted as a credential under the ENS profile.",
                "Sign in with an account. To recover access, sign in with the master "
                "token on the console's sign-in page, or use 'noust user' as root.",
                action="auth.break_glass",
            )
        return master_payload(client_ip, generation, grant)

    return None


def _audit_refusal(client_ip: str, resource: str, refusal: CredentialRefused) -> None:
    """
    Record a real credential presented where it is not accepted.

    Args:
        client_ip: Address it came from.
        resource: Path being reached.
        refusal: The refusal, carrying its action and reason.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action=refusal.action,
            result="denied",
            client_ip=client_ip,
            resource=resource,
            detail=refusal.reason,
        )


def _audit_master_token_use(client_ip: str, resource: str, payload: Mapping[str, Any]) -> None:
    """
    Warn in the audit log that the master token itself was used as a credential.

    Every use is recorded: it is the one credential shared by whoever has
    root on this machine, and once accounts exist it is the break-glass one.

    Args:
        client_ip: Address it came from.
        resource: Path being reached.
        payload: The master token's payload.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.break_glass",
            result="warning",
            client_ip=client_ip,
            actor="master",
            resource=resource,
            detail=(
                f"master token used directly as a credential ({payload.get('grant')}, "
                f"via {payload.get('source') or 'a header'})"
            ),
        )


def verify_credential(
    credential: str,
    client_ip: str,
    *,
    resource: str,
    source: str,
    connection: HTTPConnection | None = None,
) -> dict[str, Any] | None:
    """
    Verify one credential, counting the failure when it does not match.

    Args:
        credential: A session token or the master token.
        client_ip: Address presenting it.
        resource: Path being reached, for the audit record.
        source: Channel the credential arrived on: ``"cookie"`` or ``"bearer"``.
        connection: The request carrying it, which a fleet token is admitted
            against; a fleet token verified without one is refused.

    Returns:
        The session payload, tagged with the channel it arrived on under
        ``"source"`` for the record, or None when the credential is not valid.
        No policy reads the channel: CSRF and sudo mode follow the
        credential's ``"type"``, so a session cannot shed either by moving
        from the cookie to the header.

    Raises:
        CredentialRefused: When a real credential is refused on this request
            (a fleet token from anywhere but the tunnel, an API token outside
            its networks, the master token under the ENS profile). Audited,
            and not counted as a guess: the credential is real.
    """
    try:
        payload = check_credential(credential, client_ip, connection)
    except CredentialRefused as exc:
        _audit_refusal(client_ip, resource, exc)
        _mark_counted(credential)
        raise
    if payload is None:
        if _is_guess(credential):
            record_auth_failure(
                client_ip, resource, source, lockout_key=failure_key(credential, client_ip)
            )
            # The rate limiter may have noted the same guess; it is paid for.
            _mark_counted(credential)
        return None
    payload["source"] = source
    if payload.get("type") == "master":
        _audit_master_token_use(client_ip, resource, payload)
    return payload


def authenticate_connection(
    connection: HTTPConnection, ticket: str | None = None
) -> dict[str, Any] | None:
    """
    Authenticate a WebSocket handshake from every credential it may carry.

    Order: session cookie, ``wasm.token.<token>`` subprotocol, ``Authorization:
    Bearer``, then a single-use ticket from ``POST /api/auth/ws-ticket``. A
    handshake that presents nothing usable counts as exactly one failure, not
    one per channel tried.

    Args:
        connection: The pending handshake.
        ticket: Single-use ticket from the query string, if any.

    Returns:
        The session payload, or None when the handshake is not authenticated.
    """
    manager = get_global_token_manager()
    if manager is None:
        return None

    config = get_security_config()
    client_ip = get_client_ip(connection, config)
    resource = connection.scope.get("path", "")

    candidates = (
        connection.cookies.get(SESSION_COOKIE_NAME),
        subprotocol_token(connection),
        bearer_token(connection),
    )
    try:
        for credential in candidates:
            if not credential:
                continue
            payload = check_credential(credential, client_ip, connection)
            if payload is not None:
                return payload

        if ticket:
            payload = manager.consume_ws_ticket(ticket, client_ip)
            if payload is not None and is_fleet(payload):
                return admit_fleet(payload, connection)
            if payload is not None:
                return payload
    except CredentialRefused as exc:
        _audit_refusal(client_ip, resource, exc)
        _mark_counted(*candidates)
        return None

    # A handshake that presented only retired fleet tokens is a central that
    # has not heard yet, not a guess from the address (see failure_key).
    presented = [credential for credential in candidates if credential]
    keys = {failure_key(credential, client_ip) for credential in presented}
    key = keys.pop() if len(keys) == 1 and not ticket else client_ip
    record_auth_failure(client_ip, resource, "websocket", lockout_key=key)
    # One failure per handshake, whichever channels the rate limiter noted.
    _mark_counted(*candidates)
    return None


def _check_csrf(request: Request, payload: dict[str, Any], client_ip: str) -> None:
    """
    Enforce the CSRF token on cookie-authenticated mutations.

    Args:
        request: The incoming request.
        payload: The verified session payload.
        client_ip: The caller's address, for the audit record.

    Raises:
        HTTPException: 403 when the CSRF token is missing or wrong.
    """
    if request.method.upper() in SAFE_METHODS:
        return

    presented = request.headers.get(CSRF_HEADER_NAME, "")
    expected = payload.get("csrf", "")
    if presented and expected and secrets.compare_digest(presented, expected):
        return

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="auth.csrf",
            result="denied",
            client_ip=client_ip,
            actor=actor_label(payload),
            resource=request.url.path,
            detail=f"missing or invalid {CSRF_HEADER_NAME} header",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Missing or invalid CSRF token. Send the {CSRF_COOKIE_NAME} cookie value "
            f"in the {CSRF_HEADER_NAME} header, or authenticate with a Bearer token."
        ),
    )


def ensure_permission(request: Request, payload: Mapping[str, Any], permission: str) -> None:
    """
    Refuse a request whose principal lacks a permission, and audit the refusal.

    The route's own permission is enforced by :func:`require_auth`; a handler
    whose need depends on what it is asked calls this for the rest.

    Args:
        request: The incoming request.
        payload: The authenticated payload.
        permission: A :class:`~noust.web.permissions.Permission` value.

    Raises:
        PermissionDenied: 403, per
            :func:`noust.web.permissions.enforce.check_permission`.
    """
    try:
        check_permission(payload, permission)
    except PermissionDenied as exc:
        # A person told to enrol a factor or accept the notice is not being
        # refused anything; auditing each poll the console makes meanwhile
        # would bury the refusals that matter.
        if exc.error == "permission_denied":
            _audit_permission_refusal(request, payload, exc.reason)
        raise


def _audit_permission_refusal(request: Request, payload: Mapping[str, Any], reason: str) -> None:
    """
    Record a request refused for want of a permission.

    Args:
        request: The request.
        payload: Who made it.
        reason: The sentence it was refused with.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.scope",
            result="denied",
            client_ip=get_client_ip(request),
            actor=actor_label(payload),
            resource=request.url.path,
            detail=reason,
        )


def authorize(request: Request, payload: Mapping[str, Any], needed: list[str] | None) -> None:
    """
    Hold a principal to everything its route needs.

    Args:
        request: The incoming request.
        payload: The authenticated payload.
        needed: What :func:`~noust.web.permissions.enforce.required_permissions`
            answered; None for a route no map names.

    Raises:
        PermissionDenied: 403 when a permission is missing, or the route
            declares none: a route nobody classified is refused, not served.
    """
    if needed is None:
        route = f"{request.method} {request.url.path}"
        logger.error("No permission is declared for %s; refusing it", route)
        _audit_permission_refusal(request, payload, f"{route} declares no permission")
        raise PermissionDenied(
            "permission_undeclared",
            "",
            f"{route} declares no permission",
            "This is a defect in Noust: every route must name its permission in "
            "noust.web.permissions. Report it; the route is refused until then.",
        )
    for permission in needed:
        ensure_permission(request, payload, permission)


async def require_auth(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency enforcing authentication and permissions on an endpoint.

    Installed on the API router itself, so every route under ``/api`` runs it
    whether or not its handler asks for the payload, and every handler that
    does gets the same payload - FastAPI resolves a dependency once per
    request. A route the permission maps call public (the sign-in, the
    invitation pages) is let through with an empty payload and no credential
    is looked at.

    Accepts, in order, an ``Authorization: Bearer`` session token, API token
    or master token (for the CLI), or the session cookie. A session - in the
    cookie or in the header - additionally requires its CSRF header on every
    unsafe method; the master token and API tokens are not sessions and do
    not have one.

    Whichever channel is used, a credential that does not match is counted by
    :func:`record_auth_failure`, so the lockout applies to master token guessing
    on any endpoint and not only to ``/api/auth/login``. The principal is then
    held to the permission its route declares
    (:mod:`noust.web.permissions`), here, at the same chokepoint, so a new
    endpoint is covered the moment it is mapped and refused until it is.

    Args:
        request: The incoming request.

    Returns:
        The session payload, also stored on ``request.state.session``; empty
        for a public route.

    Raises:
        HTTPException: 401 when unauthenticated, 403 on a CSRF failure or a
            missing permission, 500 when the server was never initialised.
    """
    needed = await required_permissions(request)
    if needed == [PUBLIC]:
        return {}

    manager = get_global_token_manager()
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication system not initialized",
        )

    config = get_security_config()
    client_ip = get_client_ip(request, config)
    resource = request.url.path

    bearer = bearer_token(request)
    if bearer:
        payload = verify_credential(
            bearer, client_ip, resource=resource, source="bearer", connection=request
        )
        if payload is None:
            raise _unauthorized("Invalid or expired authentication token")
        if payload.get("type") == "session":
            # A session is held to the session's rules whichever header
            # carries it: the CSRF token here, sudo mode in ensure_elevated.
            # Only the master token and API tokens, which are no session, go
            # without. A client that logged in with ``bearer: true`` received
            # the CSRF token in the same response as the session token.
            _check_csrf(request, payload, client_ip)
        request.state.session = payload
        # A fleet token's refusals name why a central may never do this, which
        # says more than the permission it also lacks.
        await ensure_fleet_allowed(request, payload)
        authorize(request, payload, needed)
        return payload

    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        payload = verify_credential(
            cookie, client_ip, resource=resource, source="cookie", connection=request
        )
        if payload is None:
            raise _unauthorized("Session expired or revoked. Please log in again.")
        _check_csrf(request, payload, client_ip)
        request.state.session = payload
        await ensure_fleet_allowed(request, payload)
        authorize(request, payload, needed)
        renewed = manager.renew_session(payload)
        if renewed is not None:
            request.state.renewed_session = renewed
        return payload

    raise _unauthorized("Not authenticated")
