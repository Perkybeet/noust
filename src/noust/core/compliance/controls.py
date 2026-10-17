# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The compliance controls: the unit every framework is made of (item 83).

A control is one technical thing Noust can require: an approval by a second
person, a reason for every change, an idle limit on sessions, encrypted backups.
Each has its own setting, and by default every one is as permissive as the
``standard`` profile always was. A *framework* (:mod:`noust.core.compliance.frameworks`)
is a preset that turns some of them on and names the clause each one serves;
several frameworks may be on at once, and the value in force is the strictest of
them and of the operator's own setting (:mod:`noust.core.compliance.resolve`).

This module is pure data and comparison, with no store and no configuration
behind it, so everything that enforces a control, ``noust compliance``, the
console and the generated ``docs/COMPLIANCE.md`` agree on one definition
(rule 3).

**Kinds and strictness.** Every control has a direction in which it is stricter:

- ``flag``: on is stricter than off.
- ``choice``: the last of ``choices`` is the strictest (``allowed`` before
  ``refused``).
- ``min``: a smaller number is stricter (idle minutes); ``None`` is no limit,
  the weakest.
- ``max``: a larger number is stricter (password length, audit retention);
  ``None`` is none, the weakest.

What is **not** a control: sudo mode itself ("Confirm it's you" before a
destructive action), a person never changing or elevating their own account, and
the second factor every account enrols. They are how the console stays safe for
one person and are the same under every configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The kinds, and which end of each is the strict one (see the module docstring).
KIND_FLAG = "flag"
KIND_CHOICE = "choice"
KIND_MIN = "min"
KIND_MAX = "max"

#: How the screen groups controls.
GROUP_ACCESS = "access"
GROUP_ACCOUNTABILITY = "accountability"
GROUP_BACKUP = "backup"
GROUP_NETWORK = "network"
GROUPS: tuple[str, ...] = (GROUP_ACCESS, GROUP_ACCOUNTABILITY, GROUP_BACKUP, GROUP_NETWORK)

#: What the operator's own setting means for a control.
#: ``always``: it is the control's own value everywhere (``auth.session.idle_minutes``).
#: ``when_forced``: it only tightens a limit a framework imposes (a token's longest
#: life); on its own it limits nothing, so a default in the file is not a limit.
SETTING_ALWAYS = "always"
SETTING_WHEN_FORCED = "when_forced"

Value = Any


@dataclass(frozen=True)
class Control:
    """
    One compliance control.

    Attributes:
        id: Its stable name, in the API, the CLI, the console's catalogs and the
            documentation.
        group: One of :data:`GROUPS`.
        kind: One of the ``KIND_*`` values.
        standard: What it is when nothing is chosen: the ``standard`` profile's
            value, permissive.
        key: The configuration key that holds the operator's own value ("" when
            only a framework sets it).
        choices: For a choice, from the weakest to the strictest.
        unit: What a number counts, for display: ``minutes``, ``hours``, ``days``,
            ``characters``, ``attempts``.
        setting: One of the ``SETTING_*`` values.
        on_value: What the key holds when a flag is on (``refuse`` for
            ``audit.on_failure``).
        off_value: What it holds when it is off.
        zero_is_off: A number of 0 in the setting means no limit.
        check_only: Nothing refuses anything for this control; ``noust compliance
            check`` reads it against how the server is set up (an audit
            destination off the machine, a backup stored elsewhere).
    """

    id: str
    group: str
    kind: str
    standard: Value
    key: str = ""
    choices: tuple[str, ...] = ()
    unit: str = ""
    setting: str = SETTING_ALWAYS
    on_value: Value = True
    off_value: Value = False
    zero_is_off: bool = False
    check_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The definition, JSON-ready.
        """
        return {
            "id": self.id,
            "group": self.group,
            "kind": self.kind,
            "standard": self.standard,
            "key": self.key,
            "choices": list(self.choices),
            "unit": self.unit,
            "check_only": self.check_only,
        }

    # ----------------------------------------------------------------- strictness

    def rank(self, value: Value) -> float:
        """
        Place a value on the control's scale, so a bigger rank is stricter.

        Args:
            value: A value of this control.

        Returns:
            Its rank; the weakest possible value ranks lowest.
        """
        if self.kind == KIND_FLAG:
            return 1.0 if value else 0.0
        if self.kind == KIND_CHOICE:
            return float(self.choices.index(value)) if value in self.choices else 0.0
        if self.kind == KIND_MIN:
            return float("-inf") if value is None else -float(value)
        if value is None:
            return float("-inf")
        return float(value)

    @property
    def weakest(self) -> Value:
        """
        The value that asks for nothing: what a control is when no framework
        asks for it and the operator limited nothing.
        """
        if self.kind == KIND_FLAG:
            return False
        if self.kind == KIND_CHOICE:
            return self.choices[0]
        return None

    def stricter(self, first: Value, second: Value) -> Value:
        """
        Args:
            first: A value of this control.
            second: Another.

        Returns:
            The stricter of the two; ``first`` when they are equal.
        """
        return second if self.rank(second) > self.rank(first) else first

    def strictest(self, *values: Value) -> Value:
        """
        Args:
            *values: At least one value of this control.

        Returns:
            The strictest of them.
        """
        found = values[0]
        for value in values[1:]:
            found = self.stricter(found, value)
        return found

    def satisfies(self, value: Value, required: Value) -> bool:
        """
        Args:
            value: What is in force.
            required: What a framework asks for.

        Returns:
            Whether ``value`` is at least as strict as ``required``.
        """
        return self.rank(value) >= self.rank(required)

    def is_stricter_than_standard(self, value: Value) -> bool:
        """
        Args:
            value: A value of this control.

        Returns:
            Whether it asks for more than the default does.
        """
        return self.rank(value) > self.rank(self.standard)

    def format(self, value: Value) -> str:
        """
        Args:
            value: One of this control's values.

        Returns:
            How a person reads it, with its unit: ``on``, ``15 minutes``, ``no limit``.
        """
        if self.kind == KIND_FLAG:
            return "on" if value else "off"
        if value is None:
            return "no limit" if self.kind == KIND_MIN else "none"
        return f"{value} {self.unit}" if self.unit else str(value)

    # ------------------------------------------------------------------- parsing

    def coerce(self, raw: Value) -> Value:
        """
        Read a value as it arrives from a file, a form or a command line.

        Args:
            raw: What was typed or stored.

        Returns:
            A value of this control.

        Raises:
            ValueError: When it is not one.
        """
        if self.kind == KIND_FLAG:
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in (str(self.on_value).lower(), "true", "yes", "on", "1"):
                return True
            if text in (str(self.off_value).lower(), "false", "no", "off", "0", ""):
                return False
            raise ValueError(f"{self.id} is on or off")
        if self.kind == KIND_CHOICE:
            text = str(raw).strip().lower()
            if text not in self.choices:
                raise ValueError(f"{self.id} is one of: {', '.join(self.choices)}")
            return text
        if raw is None or str(raw).strip().lower() in ("", "none", "off", "unlimited"):
            return None
        if isinstance(raw, bool):
            raise ValueError(f"{self.id} is a number of {self.unit or 'units'}")
        try:
            number = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(f"{self.id} is a number of {self.unit or 'units'}") from exc
        if number < 0:
            raise ValueError(f"{self.id} is not negative")
        if number == 0 and self.zero_is_off:
            return None
        return number

    def stored(self, value: Value) -> Value:
        """
        Args:
            value: A value of this control.

        Returns:
            What the control's key holds for it.
        """
        if self.kind == KIND_FLAG:
            return self.on_value if value else self.off_value
        if self.kind in (KIND_MIN, KIND_MAX) and value is None:
            return 0 if self.zero_is_off else None
        return value


def _flag(control_id: str, group: str, key: str, **extra: Any) -> Control:
    return Control(control_id, group, KIND_FLAG, False, key, **extra)


def _choice(
    control_id: str, group: str, key: str, choices: tuple[str, ...], **extra: Any
) -> Control:
    return Control(control_id, group, KIND_CHOICE, choices[0], key, choices, **extra)


#: Every control, in the order the console and the documentation list them.
CONTROLS: tuple[Control, ...] = (
    # ---- Access and accounts
    _flag("roles_split", GROUP_ACCESS, "security.roles_split"),
    _flag("separation_of_duties", GROUP_ACCESS, "security.separation_of_duties"),
    _flag("single_role_per_account", GROUP_ACCESS, "security.single_role_per_account"),
    _flag("sso_no_admin", GROUP_ACCESS, "security.sso_no_admin"),
    _flag("approvals", GROUP_ACCESS, "approval.enabled"),
    _flag("restricted_tokens", GROUP_ACCESS, "security.restricted_tokens"),
    Control(
        "token_max_days",
        GROUP_ACCESS,
        KIND_MIN,
        None,
        "auth.tokens.max_days",
        unit="days",
        setting=SETTING_WHEN_FORCED,
    ),
    _choice(
        "master_token",
        GROUP_ACCESS,
        "security.master_token",
        ("break_glass", "recovery"),
    ),
    _flag("sudo_password", GROUP_ACCESS, "auth.sudo.require_password"),
    Control(
        "sudo_idle_minutes", GROUP_ACCESS, KIND_MIN, 15, "auth.sudo.idle_minutes", unit="minutes"
    ),
    Control(
        "sudo_max_minutes", GROUP_ACCESS, KIND_MIN, 120, "auth.sudo.max_minutes", unit="minutes"
    ),
    Control(
        "session_idle_minutes",
        GROUP_ACCESS,
        KIND_MIN,
        30,
        "auth.session.idle_minutes",
        unit="minutes",
    ),
    Control(
        "session_absolute_hours",
        GROUP_ACCESS,
        KIND_MIN,
        12,
        "auth.session.absolute_hours",
        unit="hours",
    ),
    Control(
        "lockout_threshold", GROUP_ACCESS, KIND_MIN, 5, "auth.lockout.threshold", unit="attempts"
    ),
    Control("lockout_minutes", GROUP_ACCESS, KIND_MAX, 15, "auth.lockout.minutes", unit="minutes"),
    Control(
        "password_min_length",
        GROUP_ACCESS,
        KIND_MAX,
        12,
        "auth.password.min_length",
        unit="characters",
    ),
    Control(
        "access_review_days",
        GROUP_ACCESS,
        KIND_MIN,
        None,
        "security.access_review_days",
        unit="days",
        check_only=True,
    ),
    Control(
        "stale_account_days",
        GROUP_ACCESS,
        KIND_MIN,
        None,
        "security.stale_account_days",
        unit="days",
        check_only=True,
    ),
    # ---- Accountability
    _flag("change_reasons", GROUP_ACCOUNTABILITY, "security.change_reasons"),
    _flag("exception_reasons", GROUP_ACCOUNTABILITY, "security.exception_reasons"),
    Control(
        "audit_retention_days",
        GROUP_ACCOUNTABILITY,
        KIND_MAX,
        90,
        "audit.retention_days",
        unit="days",
    ),
    _flag(
        "audit_fail_closed",
        GROUP_ACCOUNTABILITY,
        "audit.on_failure",
        on_value="refuse",
        off_value="warn",
    ),
    _flag(
        "external_audit_sink", GROUP_ACCOUNTABILITY, "security.external_audit_sink", check_only=True
    ),
    Control(
        "audit_review_days",
        GROUP_ACCOUNTABILITY,
        KIND_MIN,
        None,
        "security.audit_review_days",
        unit="days",
        check_only=True,
    ),
    _flag("log_shipping_tls", GROUP_ACCOUNTABILITY, "security.log_shipping_tls"),
    # ---- Backups
    _choice(
        "backup_encryption",
        GROUP_BACKUP,
        "backup.encryption",
        ("optional", "required"),
    ),
    Control(
        "backup_verify_days",
        GROUP_BACKUP,
        KIND_MIN,
        7,
        "backup.verify_days",
        unit="days",
        zero_is_off=True,
    ),
    Control(
        "backup_max_age_hours",
        GROUP_BACKUP,
        KIND_MIN,
        26,
        "security.backup_max_age_hours",
        unit="hours",
        check_only=True,
    ),
    _flag("backup_offsite", GROUP_BACKUP, "security.backup_offsite", check_only=True),
    # ---- Network and host
    _choice(
        "tls_certificate",
        GROUP_NETWORK,
        "security.tls_certificate",
        ("any", "operator"),
        check_only=True,
    ),
    Control(
        "certificate_min_days",
        GROUP_NETWORK,
        KIND_MAX,
        30,
        "security.certificate_min_days",
        unit="days",
        check_only=True,
    ),
    _flag("explicit_allowlist", GROUP_NETWORK, "security.explicit_allowlist", check_only=True),
    _choice(
        "console_exposure",
        GROUP_NETWORK,
        "security.console_exposure",
        ("allowed", "refused"),
    ),
    _choice(
        "database_remote_listen",
        GROUP_NETWORK,
        "security.database_remote_listen",
        ("allowed", "refused"),
    ),
    _choice(
        "server_terminal",
        GROUP_NETWORK,
        "security.server_terminal",
        ("allowed", "refused"),
    ),
    Control(
        "terminal_idle_minutes",
        GROUP_NETWORK,
        KIND_MIN,
        120,
        "terminal.idle_minutes",
        unit="minutes",
    ),
    Control(
        "terminal_max_minutes", GROUP_NETWORK, KIND_MIN, 720, "terminal.max_minutes", unit="minutes"
    ),
    _flag("mounts_read_only", GROUP_NETWORK, "security.mounts_read_only"),
    _flag("db_write_root_equivalent", GROUP_NETWORK, "security.db_write_root_equivalent"),
)

_BY_ID: dict[str, Control] = {control.id: control for control in CONTROLS}

#: The ids, for a command's choices and a request's validation.
CONTROL_IDS: tuple[str, ...] = tuple(_BY_ID)


def get(control_id: str) -> Control:
    """
    Args:
        control_id: A control's id.

    Returns:
        The control.

    Raises:
        KeyError: When there is none; callers validate against :data:`CONTROL_IDS`.
    """
    return _BY_ID[control_id]


def exists(control_id: str) -> bool:
    """
    Args:
        control_id: A name.

    Returns:
        Whether it is a control.
    """
    return control_id in _BY_ID


#: What each control does, in a sentence, for the documentation and the command
#: line. The console words them from its own catalogs.
DESCRIPTIONS: dict[str, str] = {
    "roles_split": (
        "An administrator does not govern accounts, security settings or the audit trail: "
        "those belong to the security and auditor roles."
    ),
    "separation_of_duties": (
        "One person cannot hold the account that runs the server and the one that governs "
        "it, and an auditor holds nothing else, unless a documented exception is recorded."
    ),
    "single_role_per_account": (
        "An account has one role: roles on the whole server are not added to it, and a "
        "person with two duties holds two accounts."
    ),
    "sso_no_admin": (
        "An identity provider never hands out the admin role, by default or by group: "
        "a person grants it."
    ),
    "approvals": (
        "Root-equivalent actions, servers and role changes wait for a second person to approve them."
    ),
    "restricted_tokens": (
        "An API token belongs to an account, cannot act in sudo mode and never holds "
        "root-equivalent, account or security permissions."
    ),
    "token_max_days": "The longest life of an API token, in days; an expiry becomes mandatory.",
    "master_token": (
        "What the master token may do once accounts exist: the console as break-glass, "
        "or only get a person back in."
    ),
    "sudo_password": "Confirming sudo mode asks for the account's password as well as a second factor.",
    "sudo_idle_minutes": "How long sudo mode stays open without an elevated action in it.",
    "sudo_max_minutes": "The longest sudo mode lasts from its confirmation, however busy the session.",
    "session_idle_minutes": "A console session unused this long is over.",
    "session_absolute_hours": "A session ends this long after sign-in, however active it is.",
    "lockout_threshold": "Consecutive refused sign-ins that lock an account.",
    "lockout_minutes": "How long that lock lasts at least.",
    "password_min_length": "Shortest password accepted.",
    "access_review_days": (
        "How often accounts and roles must be reviewed; `noust ens access-review` records it."
    ),
    "stale_account_days": "An account unused this long is reported as stale.",
    "change_reasons": "Every change from the command line, and every approval request, says why.",
    "exception_reasons": (
        "A decision that lowers protection - building as root, accepting a risk, a "
        "privileged container, an incident freeze - says why."
    ),
    "audit_retention_days": "The least number of days audit events are kept.",
    "audit_fail_closed": "A change is refused while the audit trail cannot be written.",
    "external_audit_sink": (
        "The audit trail is sent to a destination off the machine (syslog, or journald forwarded)."
    ),
    "audit_review_days": "How often the audit log must be reviewed; `noust audit review` records it.",
    "log_shipping_tls": "Logs leave the server only over TLS.",
    "backup_encryption": "Backups leave the server only encrypted.",
    "backup_verify_days": (
        "How often a backup is proved restorable; 0 turns it off where no framework asks."
    ),
    "backup_max_age_hours": "The newest backup of an application must be younger than this.",
    "backup_offsite": "At least one backup is kept apart, in a different place from the server.",
    "tls_certificate": (
        "The console serves a certificate of the organisation's PKI, not a self-signed one."
    ),
    "certificate_min_days": "A certificate expiring sooner than this many days is a finding.",
    "explicit_allowlist": "A console reachable beyond loopback names who may connect.",
    "console_exposure": "Whether `noust console expose` may publish the console on a public name.",
    "database_remote_listen": "Whether a database engine may listen beyond loopback.",
    "server_terminal": "Whether Noust may open a root shell on the server in the browser.",
    "terminal_idle_minutes": "A console terminal with nothing typed this long is ended.",
    "terminal_max_minutes": "The longest a console terminal lasts in all.",
    "mounts_read_only": "A server path bound into an application is read-only.",
    "db_write_root_equivalent": "Writing SQL from the console needs the root-equivalent permission.",
}


def describe(control_id: str) -> str:
    """
    Args:
        control_id: A control's id.

    Returns:
        What it does, in a sentence.
    """
    return DESCRIPTIONS[control_id]
