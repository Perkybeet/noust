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

Since 3.4 this is the *legacy view* of :mod:`noust.core.compliance`, where every
value of :class:`ProfileDefaults` is a control of its own and ``ens-medium`` is the
preset called ENS MEDIA (one of several frameworks, which may be on together).
``security.profile`` keeps working and means "ENS MEDIA is enabled";
:func:`active_defaults` is the floor the enabled frameworks leave, and
:func:`current_profile` and :func:`is_ens` say whether an ENS framework is on.

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
        console_exposure: Whether ``noust console expose`` may publish the
            console on a name of its own (mp.com.1): the console is
            root-equivalent, so under the profile it stays on loopback and is
            reached through an SSH tunnel unless the operator forces it in so
            many words, and then only with an explicit allow-list.
        sudo_idle_minutes: How long sudo mode ("Confirm it's you") stays open
            without an elevated action being performed in it. Every elevated
            action extends it by this much, up to ``sudo_max_minutes``.
        sudo_max_minutes: The longest sudo mode lasts from the moment it was
            confirmed, however busy the session is.
        sudo_require_password: Whether re-confirming also asks for the
            account's password. Outside the profile one factor is enough: the
            session already proved the password and a second factor at sign-in.
        terminal_idle_minutes: The most a console terminal may go with nothing
            typed before it is ended (op.acc.6.4): a terminal left open is a
            session at a prompt.
        terminal_max_minutes: The most a console terminal may last in all.
        audit_fail_closed: Whether a change is refused while the audit trail
            cannot be written (op.exp.8): under the profile no change goes
            unaudited (see :mod:`noust.core.audit.failclosed`).
        server_terminal: Whether Noust may open a root shell on the server in
            the browser (``terminal.server.enabled``). Under the profile it is
            refused unless the operator forces it in so many words
            (``terminal.server.force_ens``), because it is the console at its
            most root-equivalent.
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
    console_exposure: bool = True
    sudo_idle_minutes: int = 15
    sudo_max_minutes: int = 120
    sudo_require_password: bool = False
    terminal_idle_minutes: int = 120
    terminal_max_minutes: int = 720
    server_terminal: bool = True
    audit_fail_closed: bool = False

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
    console_exposure=True,
    sudo_idle_minutes=15,
    sudo_max_minutes=120,
    sudo_require_password=False,
    terminal_idle_minutes=120,
    terminal_max_minutes=720,
    server_terminal=True,
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
    console_exposure=False,
    # Tighter than the session's own 15 minutes of idleness (the old fixed
    # window was 10), a short ceiling on standing destructive power, and the
    # password again on top of the code: the extra friction ENS accepts that
    # the standard profile does not.
    sudo_idle_minutes=10,
    sudo_max_minutes=30,
    sudo_require_password=True,
    # A terminal is a session at a prompt: shorter than the sign-in's own idle
    # lock, and capped in all, as sudo mode is.
    terminal_idle_minutes=10,
    terminal_max_minutes=60,
    server_terminal=False,
    audit_fail_closed=True,
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


def read_config(config: Any | None, key: str, default: Any) -> Any:
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
    The profile in force, as it was called before frameworks.

    Read on every call, so ``noust config set security.profile ens-medium``
    takes effect on the next request and the next command without a restart.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        ``ens-medium`` when any ENS framework is on (this setting, or
        ``security.frameworks``), ``standard`` otherwise. The behaviour of the
        server is decided by the controls, not by this name.
    """
    return PROFILE_ENS_MEDIUM if is_ens(config) else PROFILE_STANDARD


def is_ens(config: Any | None = None) -> bool:
    """
    Report whether a framework of the ENS family is on.

    For what is about the ENS itself (``noust ens``, the evidence report). What a
    server refuses or requires is a control's, asked of
    :mod:`noust.core.compliance`.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        True when ENS BÁSICA, MEDIA or ALTA is enabled, or ``security.profile``
        says ``ens-medium``.
    """
    from noust.core.compliance import frameworks, resolve

    return any(frameworks.get(name).family == "ens" for name in resolve.enabled_ids(config))


#: Which control each field of :class:`ProfileDefaults` is.
_FIELD_CONTROLS: dict[str, str] = {
    "idle_minutes": "session_idle_minutes",
    "absolute_hours": "session_absolute_hours",
    "lockout_threshold": "lockout_threshold",
    "lockout_minutes": "lockout_minutes",
    "password_min_length": "password_min_length",
    "token_max_days": "token_max_days",
    "audit_retention_days": "audit_retention_days",
    "approvals": "approvals",
    "cli_reason_required": "change_reasons",
    "master_token": "master_token",
    "backup_encryption": "backup_encryption",
    "backup_verify_days": "backup_verify_days",
    "backup_max_age_hours": "backup_max_age_hours",
    "tls_certificate": "tls_certificate",
    "certificate_min_days": "certificate_min_days",
    "external_audit_sink": "external_audit_sink",
    "explicit_allowlist": "explicit_allowlist",
    "access_review_days": "access_review_days",
    "audit_review_days": "audit_review_days",
    "stale_account_days": "stale_account_days",
    "database_remote_listen": "database_remote_listen",
    "console_exposure": "console_exposure",
    "sudo_idle_minutes": "sudo_idle_minutes",
    "sudo_max_minutes": "sudo_max_minutes",
    "sudo_require_password": "sudo_password",
    "terminal_idle_minutes": "terminal_idle_minutes",
    "terminal_max_minutes": "terminal_max_minutes",
    "server_terminal": "server_terminal",
    "audit_fail_closed": "audit_fail_closed",
}

#: Fields that say "allowed": the control's strict end, ``refused``, is False.
_ALLOWED_FIELDS = frozenset({"database_remote_listen", "console_exposure", "server_terminal"})


def defaults_from_floors(framework_ids: Any) -> ProfileDefaults:
    """
    The values the given frameworks leave, as the legacy shape.

    Args:
        framework_ids: The frameworks that are on.

    Returns:
        :data:`STANDARD`, with every field a framework asks for more of raised to
        what it asks. With only ENS MEDIA this is exactly :data:`ENS_MEDIUM`.
    """
    from dataclasses import replace

    from noust.core.compliance import controls, frameworks, resolve

    ids = list(framework_ids)
    if not ids:
        return STANDARD
    changes: dict[str, Any] = {}
    for field_name, control_id in _FIELD_CONTROLS.items():
        control = controls.get(control_id)
        required = resolve.floor_for(control_id, ids)
        if not control.is_stricter_than_standard(required):
            continue
        changes[field_name] = (required != "refused") if field_name in _ALLOWED_FIELDS else required
    ens = any(frameworks.get(name).family == "ens" for name in ids)
    found = replace(STANDARD, name=PROFILE_ENS_MEDIUM if ens else PROFILE_STANDARD, **changes)
    # The one preset that was a profile before frameworks: asked for alone, it is that profile.
    return ENS_MEDIUM if found == ENS_MEDIUM else found


def active_defaults(config: Any | None = None) -> ProfileDefaults:
    """
    The floor the enabled frameworks leave, in the legacy shape.

    The operator's own settings are not in it: callers cap their own value at
    these (``min(configured, floor)``). What is in force, setting included, is
    :func:`noust.core.compliance.resolve`.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        :data:`STANDARD` when no framework is on; with ENS MEDIA alone,
        :data:`ENS_MEDIUM`; with several, the strictest of them field by field.
    """
    from noust.core.compliance import resolve

    return defaults_from_floors(resolve.enabled_ids(config))


def backup_encryption_required(config: Any | None = None) -> bool:
    """
    Report whether a backup may leave this server only encrypted.

    ``backup.encryption: required`` asks for it on its own; a framework asks for
    it whatever the key says.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        True when unencrypted uploads are refused.
    """
    from noust.core.compliance import resolve

    return resolve.restricts("backup_encryption", config)


def database_remote_listen_allowed(config: Any | None = None) -> bool:
    """
    Report whether a database engine may be opened beyond loopback.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        False when the control is on (a framework, or ``security.database_remote_listen:
        refused``): an engine is reached through an SSH tunnel there, and ``noust
        db settings`` refuses a non-loopback ``listen_addresses``, ``bind-address``,
        ``bind`` or ``net.bindIp``.
    """
    from noust.core.compliance import resolve

    return not resolve.restricts("database_remote_listen", config)


def console_exposure_allowed(config: Any | None = None) -> bool:
    """
    Report whether the console may be published on a public name without being forced.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        False when the control is on: ``noust console expose`` refuses unless the
        operator confirms the override in so many words, and the override needs
        an allow-list of who may connect.
    """
    from noust.core.compliance import resolve

    return not resolve.restricts("console_exposure", config)


def server_terminal_allowed(config: Any | None = None) -> bool:
    """
    Report whether a root shell on the server may be opened without being forced.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        False when the control is on: the server's terminal stays off unless the
        operator sets ``terminal.server.force_ens`` as well as
        ``terminal.server.enabled``, which is on record.
    """
    from noust.core.compliance import resolve

    return not resolve.restricts("server_terminal", config)


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
            "console exposure",
            "Noust may publish the console on a public name of its own (noust console expose).",
            "allowed, with two-factor on every account",
            "refused unless forced, and then only with an allow-list of who may connect",
            ("mp.com.1", "op.acc.4.5"),
        ),
        BaselineItem(
            "terminal.*",
            "How long a console terminal may last, and how long it may go with nothing typed.",
            f"{std.terminal_idle_minutes} min idle, {std.terminal_max_minutes} min in all "
            "(the configured values)",
            f"{ens.terminal_idle_minutes} min idle, {ens.terminal_max_minutes} min in all (at most)",
            ("op.acc.6.4", "op.exp.8"),
        ),
        BaselineItem(
            "terminal.server.enabled",
            "Noust may open a root shell on the server in the browser.",
            "off until the operator turns it on",
            "refused unless forced with terminal.server.force_ens, and then on record",
            ("op.acc.3", "mp.eq.1"),
        ),
        BaselineItem(
            "web.ip_whitelist",
            "A console reachable beyond loopback names who may connect.",
            "optional",
            "explicit",
            ("mp.com.1", "op.acc.4.5"),
        ),
    ]
