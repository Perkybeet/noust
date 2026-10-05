# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The security profiles: ``standard`` and ``ens-medium``, stated once.

``security.profile: ens-medium`` is not a switch that turns features on; it is
a set of values and refusals that the areas which own each setting read from
here (rule 3): the sign-in policy (:mod:`noust.core.accounts.policy`), the
CLI's ``--reason`` (:mod:`noust.cli.audit_policy`), four-eyes approvals
(:mod:`noust.core.accounts.approvals`), the audit retention
(:mod:`noust.core.audit.settings`), backup encryption
(:mod:`noust.managers.backup_destinations`) and the checks of ``noust ens
check`` (:mod:`noust.core.ens.checks`).

The numbers are the ones the 3.1 spec decided (§12.7). The RD does not fix
them; the organisation's policy does (op.acc.6.4, mp.eq.2.1 "tiempo
prudencial", op.exp.8.r3.1). Under the profile each is a limit the operator
may tighten but not loosen: a configured idle time of 60 minutes is applied as
15, a retention of 120 days as 365.

A typo in the profile's name fails strict: anything that is neither empty nor
``standard`` is read as ``ens-medium``, so a misspelt profile can never quietly
switch the profile off.

This module imports nothing from Noust at load time: the sign-in policy and
the CLI read it while the rest of Noust is still being imported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PROFILE_STANDARD = "standard"
PROFILE_ENS_MEDIUM = "ens-medium"
PROFILES: tuple[str, ...] = (PROFILE_STANDARD, PROFILE_ENS_MEDIUM)

#: Spellings of the ENS profile an operator may reasonably type.
_ENS_SPELLINGS = frozenset({"ens-medium", "ens_medium", "ens", "ens-media", "medium"})

#: The ranges, in minutes, ``auth.sudo.idle_minutes`` and ``auth.sudo.max_minutes``
#: accept, and the sign-in policy clamps a hand-edited file to: shorter than the
#: first and the confirmation is a nuisance operators route around; longer than
#: the second and sudo mode is no longer "recently".
SUDO_IDLE_RANGE: tuple[int, int] = (5, 60)
SUDO_MAX_RANGE: tuple[int, int] = (5, 480)

#: What the master token may do once accounts exist.
MASTER_BREAK_GLASS = "break_glass"
#: Under the profile: only getting a person back in (spec §2.2).
MASTER_RECOVERY = "recovery"


@dataclass(frozen=True)
class ProfileDefaults:
    """
    What one profile fixes.

    Attributes:
        name: ``standard`` or ``ens-medium``.
        idle_minutes: A console session unused this long is over (mp.eq.2).
        absolute_hours: A session is over this long after sign-in.
        lockout_threshold: Consecutive refused sign-ins that lock an account
            (op.acc.6.8).
        lockout_minutes: How long that lock lasts at least.
        password_min_length: Shortest password accepted (op.acc.6.r1).
        token_max_days: Longest life of an API token; None for no limit.
        audit_retention_days: Least number of days audit events are kept
            (op.exp.8.r3).
        approvals: Whether root-equivalent actions need a second person
            (op.acc.3, op.exp.5.4).
        cli_reason_required: Whether every CLI command that changes something
            needs ``--reason`` (op.exp.5.1).
        master_token: What the master token may do once accounts exist:
            ``break_glass`` (the console, audited) or ``recovery`` (only
            getting a person back in).
        backup_encryption: ``optional`` or ``required``: under ``required``
            nothing leaves the server unencrypted (mp.si.2).
        backup_verify_days: A backup verification older than this is overdue
            (mp.info.6.r1).
        backup_max_age_hours: A newest backup older than this is overdue.
        tls_certificate: ``any`` or ``operator``: under ``operator`` the
            console is expected to serve a certificate the organisation's PKI
            issued, not a self-signed one (mp.com.2.r1).
        certificate_min_days: A certificate expiring sooner is a finding.
        external_audit_sink: Whether an audit destination off the machine is
            expected (op.mon.3.1, op.exp.8.r4).
        explicit_allowlist: Whether a console answering beyond loopback must
            name who may connect (mp.com.1, op.acc.4.5).
        access_review_days: An access review older than this is overdue
            (op.acc.4.4).
        audit_review_days: An audit review older than this is overdue
            (op.exp.8.r1).
        stale_account_days: An account unused this long is stale (op.acc.1.4).
        database_remote_listen: Whether ``noust db settings`` may open a
            database engine on an address beyond loopback (mp.com.1): under
            the profile an engine is reached through an SSH tunnel, never
            directly.
        sudo_idle_minutes: How long sudo mode ("Confirm it's you") stays open
            without an elevated action being performed in it. Every elevated
            action extends it by this much, up to ``sudo_max_minutes``.
        sudo_max_minutes: The longest sudo mode lasts from the moment it was
            confirmed, however busy the session is.
        sudo_require_password: Whether re-confirming also asks for the
            account's password. Outside the profile one factor is enough: the
            session already proved the password and a second factor at sign-in.
    """

    name: str
    idle_minutes: int
    absolute_hours: int
    lockout_threshold: int
    lockout_minutes: int
    password_min_length: int
    token_max_days: int | None
    audit_retention_days: int
    approvals: bool
    cli_reason_required: bool
    master_token: str
    backup_encryption: str
    backup_verify_days: int
    backup_max_age_hours: int
    tls_certificate: str
    certificate_min_days: int
    external_audit_sink: bool
    explicit_allowlist: bool
    access_review_days: int
    audit_review_days: int
    stale_account_days: int
    database_remote_listen: bool = True
    sudo_idle_minutes: int = 15
    sudo_max_minutes: int = 120
    sudo_require_password: bool = False

    @property
    def ens(self) -> bool:
        """Whether this is the ENS category MEDIUM profile."""
        return self.name == PROFILE_ENS_MEDIUM


#: Outside the profile: what Noust 3.1 does by default, nothing forced.
STANDARD = ProfileDefaults(
    name=PROFILE_STANDARD,
    idle_minutes=30,
    absolute_hours=12,
    lockout_threshold=5,
    lockout_minutes=15,
    password_min_length=12,
    token_max_days=None,
    audit_retention_days=90,
    approvals=False,
    cli_reason_required=False,
    master_token=MASTER_BREAK_GLASS,
    backup_encryption="optional",
    backup_verify_days=7,
    backup_max_age_hours=26,
    tls_certificate="any",
    certificate_min_days=30,
    external_audit_sink=False,
    explicit_allowlist=False,
    access_review_days=90,
    audit_review_days=7,
    stale_account_days=90,
    database_remote_listen=True,
    sudo_idle_minutes=15,
    sudo_max_minutes=120,
    sudo_require_password=False,
)

#: ENS category MEDIUM (spec §2 and §12.7).
ENS_MEDIUM = ProfileDefaults(
    name=PROFILE_ENS_MEDIUM,
    idle_minutes=15,
    absolute_hours=8,
    lockout_threshold=5,
    lockout_minutes=15,
    password_min_length=14,
    token_max_days=90,
    audit_retention_days=365,
    approvals=True,
    cli_reason_required=True,
    master_token=MASTER_RECOVERY,
    backup_encryption="required",
    backup_verify_days=7,
    # A daily schedule that ran a few minutes late is still a daily backup.
    backup_max_age_hours=26,
    tls_certificate="operator",
    certificate_min_days=30,
    external_audit_sink=True,
    explicit_allowlist=True,
    access_review_days=90,
    audit_review_days=7,
    stale_account_days=90,
    database_remote_listen=False,
    # Tighter than the session's own 15 minutes of idleness (the old fixed
    # window was 10), a short ceiling on standing destructive power, and the
    # password again on top of the code: the extra friction ENS accepts that
    # the standard profile does not.
    sudo_idle_minutes=10,
    sudo_max_minutes=30,
    sudo_require_password=True,
)


def normalise_profile(value: Any) -> str:
    """
    Read ``security.profile``, failing strict rather than open.

    Args:
        value: What the configuration holds.

    Returns:
        ``standard`` for an empty value or ``standard``; ``ens-medium`` for
        any spelling of it, and for anything unrecognised: a typo in a
        security profile must not quietly turn the profile off.
    """
    text = str(value or "").strip().lower()
    if text in ("", PROFILE_STANDARD):
        return PROFILE_STANDARD
    if text not in _ENS_SPELLINGS:
        logger.warning(
            "Unknown security.profile %r; applying %s, the stricter profile",
            value,
            PROFILE_ENS_MEDIUM,
        )
    return PROFILE_ENS_MEDIUM


def defaults_for(value: Any) -> ProfileDefaults:
    """
    The values a profile fixes.

    Args:
        value: A profile name as configured, in any spelling.

    Returns:
        :data:`ENS_MEDIUM` or :data:`STANDARD`.
    """
    return ENS_MEDIUM if normalise_profile(value) == PROFILE_ENS_MEDIUM else STANDARD


def _read(config: Any | None, key: str, default: Any) -> Any:
    """
    Read one key, tolerating a stand-in configuration that cannot answer.

    Args:
        config: A :class:`~noust.core.config.Config`, a stand-in, or None for
            the process-wide configuration.
        key: Dotted key.
        default: What to return when it cannot be read.

    Returns:
        The value, or ``default``.
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    get = getattr(config, "get", None)
    return get(key, default) if callable(get) else default


def current_profile(config: Any | None = None) -> str:
    """
    The profile in force.

    Read on every call, so ``noust config set security.profile ens-medium``
    takes effect on the next request and the next command without a restart.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        ``standard`` or ``ens-medium``.
    """
    return normalise_profile(_read(config, "security.profile", PROFILE_STANDARD))


def is_ens(config: Any | None = None) -> bool:
    """
    Report whether the ENS category MEDIUM profile is on.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        True under ``ens-medium``.
    """
    return current_profile(config) == PROFILE_ENS_MEDIUM


def active_defaults(config: Any | None = None) -> ProfileDefaults:
    """
    The values of the profile in force.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        :data:`ENS_MEDIUM` or :data:`STANDARD`.
    """
    return ENS_MEDIUM if is_ens(config) else STANDARD


def backup_encryption_required(config: Any | None = None) -> bool:
    """
    Report whether a backup may leave this server only encrypted.

    ``backup.encryption: required`` asks for it outside the profile too; the
    profile asks for it whatever the key says.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        True when unencrypted uploads are refused.
    """
    if active_defaults(config).backup_encryption == "required":
        return True
    return str(_read(config, "backup.encryption", "optional") or "").strip().lower() == "required"


def database_remote_listen_allowed(config: Any | None = None) -> bool:
    """
    Report whether a database engine may be opened beyond loopback.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        False under ``ens-medium``: an engine is reached through an SSH
        tunnel there, and ``noust db settings`` refuses a non-loopback
        ``listen_addresses``, ``bind-address``, ``bind`` or ``net.bindIp``.
    """
    return active_defaults(config).database_remote_listen


@dataclass(frozen=True)
class BaselineItem:
    """
    One value the profile fixes, as ``noust ens check`` and ``docs/ENS.md`` state it.

    Attributes:
        key: The configuration key, or the behaviour, it concerns.
        description: What it controls, in a sentence.
        standard_value: Its value outside the profile.
        ens_value: Its value under ``ens-medium``.
        measures: The RD 311/2022 measures it answers.
    """

    key: str
    description: str
    standard_value: str
    ens_value: str
    measures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "key": self.key,
            "description": self.description,
            "standard_value": self.standard_value,
            "ens_value": self.ens_value,
            "measures": list(self.measures),
        }


def _days(value: int | None) -> str:
    return "no limit" if value is None else f"{value} days"


def baseline() -> list[BaselineItem]:
    """
    Everything the profile fixes, with both values and the measure it answers.

    Returns:
        One item per value, in the order ``docs/ENS.md`` lists them.
    """
    std, ens = STANDARD, ENS_MEDIUM
    return [
        BaselineItem(
            "auth.session.idle_minutes",
            "A console session unused this long is over.",
            f"{std.idle_minutes} min",
            f"{ens.idle_minutes} min (at most)",
            ("mp.eq.2",),
        ),
        BaselineItem(
            "auth.session.absolute_hours",
            "A session ends this long after sign-in, however active.",
            f"{std.absolute_hours} h",
            f"{ens.absolute_hours} h (at most)",
            ("mp.eq.2",),
        ),
        BaselineItem(
            "auth.sudo",
            'Sudo mode ("Confirm it\'s you"): how long it stays open while it is used, the '
            "longest it lasts, and whether the password is asked again.",
            f"{std.sudo_idle_minutes} min idle, {std.sudo_max_minutes} min in all, one factor",
            (
                f"{ens.sudo_idle_minutes} min idle, {ens.sudo_max_minutes} min in all "
                "(at most), password and code"
            ),
            ("op.acc.6",),
        ),
        BaselineItem(
            "auth.lockout",
            "Consecutive refused sign-ins that lock an account, and for how long.",
            f"{std.lockout_threshold} / {std.lockout_minutes} min",
            f"{ens.lockout_threshold} / {ens.lockout_minutes} min (at least)",
            ("op.acc.6.8",),
        ),
        BaselineItem(
            "auth.password.min_length",
            "Shortest password accepted.",
            str(std.password_min_length),
            f"{ens.password_min_length} (at least)",
            ("op.acc.6.r1",),
        ),
        BaselineItem(
            "auth.tokens.max_days",
            "Longest life of an API token; an expiry is mandatory.",
            _days(std.token_max_days),
            f"{_days(ens.token_max_days)} (at most)",
            ("op.acc.1.3", "op.acc.4"),
        ),
        BaselineItem(
            "master token",
            "What the master token may do once accounts exist.",
            "break-glass: the console, audited",
            "account recovery only",
            ("op.acc.6.r8",),
        ),
        BaselineItem(
            "audit.retention_days",
            "Least number of days audit events are kept.",
            f"{std.audit_retention_days} days (floor)",
            f"{ens.audit_retention_days} days (at least)",
            ("op.exp.8.r3",),
        ),
        BaselineItem(
            "audit destinations",
            "An audit destination off the machine (journald forwarded, or syslog).",
            "optional",
            "expected",
            ("op.mon.3.1", "op.exp.8.r4"),
        ),
        BaselineItem(
            "approval.enabled",
            "Root-equivalent actions, nodes and role changes need a second person.",
            "off",
            "on",
            ("op.acc.3", "op.exp.5.4"),
        ),
        BaselineItem(
            "--reason",
            "Every CLI command that changes something states why.",
            "optional",
            "required",
            ("op.exp.5.1",),
        ),
        BaselineItem(
            "backup.encryption",
            "Backups leave the server only encrypted.",
            std.backup_encryption,
            ens.backup_encryption,
            ("mp.si.2",),
        ),
        BaselineItem(
            "backup verification",
            "The newest backup of each application was verified this recently.",
            f"{std.backup_verify_days} days",
            f"{ens.backup_verify_days} days",
            ("mp.info.6.r1",),
        ),
        BaselineItem(
            "console certificate",
            "The console serves a certificate of the organisation's PKI.",
            "any",
            "operator certificate",
            ("mp.com.2.r1", "mp.com.3.r2"),
        ),
        BaselineItem(
            "database listen addresses",
            "Noust may open a database engine on an address beyond loopback.",
            "allowed, with a warning and sudo mode",
            "refused: reach engines through an SSH tunnel",
            ("mp.com.1",),
        ),
        BaselineItem(
            "web.ip_whitelist",
            "A console reachable beyond loopback names who may connect.",
            "optional",
            "explicit",
            ("mp.com.1", "op.acc.4.5"),
        ),
    ]
