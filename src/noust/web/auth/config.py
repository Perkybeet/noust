# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's security settings and the one instance of each manager the process shares
(token manager, audit logger, brute-force protection).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core import paths
from noust.core.accounts import AuthPolicy, load_policy
from noust.core.audit import install as install_core_audit_log
from noust.core.audit.legacy import AuditLogger
from noust.web.auth.constants import (
    AUDIT_BACKUPS,
    AUDIT_LOG_NAME,
    AUDIT_MAX_BYTES,
    EVENT_STREAMS_MAX_PER_CREDENTIAL,
    JWT_EXPIRATION_HOURS,
    LOCKOUT_DURATION,
    MAX_BODY_BYTES,
    MAX_FAILED_ATTEMPTS,
    MAX_HOOK_BODY_BYTES,
    RATE_LIMIT_AUTHENTICATED_MAX_REQUESTS,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW,
    SECRET_FILE_NAME,
    SESSION_DB_NAME,
    SESSION_MAX_HOURS,
    TOKEN_FILE_NAME,
    TOTP_FILE_NAME,
    WEBHOOK_LOCKOUT_DURATION,
    WEBHOOK_MAX_FAILURES,
    WS_MAX_PER_CREDENTIAL,
)

if TYPE_CHECKING:
    from noust.web.auth.ratelimit import BruteForceProtection
    from noust.web.auth.tokens import TokenManager


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
        event_streams_max_per_credential: Event streams one credential may hold
            open at once.
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
    event_streams_max_per_credential: int = EVENT_STREAMS_MAX_PER_CREDENTIAL
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
            The explicit ``state_dir``, else ``NOUST_WEB_STATE_DIR``, else the
            configuration directory.
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
