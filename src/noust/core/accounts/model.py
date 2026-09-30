# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an account is: its roles, its states, its record, and its errors.

Pure data, with no store and no web layer behind it, so the CLI (which may run
without the console's dependencies) and the console agree on one definition.
What each role may *do* is the console's business and lives in
:mod:`noust.web.permissions.roles`; this module only knows the names and the
separation-of-duties rule between them, because that rule is enforced where
accounts are written (:class:`noust.core.accounts.manager.AccountManager`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import SecurityError, ValidationError

#: One account, one role (ENS op.acc.1.2): a person with two duties holds two
#: accounts. Weakest first, which is also the order the console lists them in.
ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
ROLE_SECURITY = "security"
ROLE_AUDITOR = "auditor"
ROLES: tuple[str, ...] = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN, ROLE_SECURITY, ROLE_AUDITOR)

#: What an account can be. ``locked`` is the per-account lockout, which ends
#: by itself at ``locked_until`` or by ``noust user unlock``; ``invited`` holds
#: no credential yet and cannot sign in until its invitation is accepted.
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_LOCKED = "locked"
STATUS_INVITED = "invited"
STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_DISABLED, STATUS_LOCKED, STATUS_INVITED)

#: Lower-case letters, digits, dot, dash and underscore, 2 to 64 characters,
#: starting with a letter or digit. No colon, so a username can never be
#: mistaken for an actor label such as ``token:<name>`` in the audit log.
USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")

#: Names that already mean something in an audit line or on this machine.
RESERVED_USERNAMES = frozenset(
    {"anonymous", "unknown", "master", "system", "root", "noust", "noust-tunnel", "fleet"}
)

#: Longest display name kept; it is shown, never parsed.
MAX_DISPLAY_NAME = 128
#: Longest person reference kept: an e-mail address or an employee number.
MAX_PERSON_REF = 254


def incompatible_roles(first: str, second: str) -> bool:
    """
    Report whether one person may not hold both roles (separation of duties).

    ``admin`` and ``operator`` run the infrastructure, ``security`` governs
    who may run it: holding both lets one person authorise their own access
    (op.acc.3.2). ``auditor`` checks everybody, including whoever else that
    person is, so it is exclusive.

    Args:
        first: A role.
        second: Another role, possibly the same.

    Returns:
        True when the two roles must not belong to accounts of one person.
    """
    pair = {first, second}
    if ROLE_AUDITOR in pair:
        return first != second
    return ROLE_SECURITY in pair and bool(pair & {ROLE_ADMIN, ROLE_OPERATOR})


def validate_username(value: str) -> str:
    """
    Normalise and check a username.

    Args:
        value: The name as typed.

    Returns:
        The name, trimmed and lower-cased.

    Raises:
        ValidationError: When it does not match :data:`USERNAME_PATTERN` or is
            reserved.
    """
    name = (value or "").strip().lower()
    if not USERNAME_PATTERN.fullmatch(name):
        raise ValidationError(
            f"Invalid username: {value!r}",
            details=(
                "Use 2 to 64 lower-case letters, digits, '.', '-' or '_', starting with a "
                "letter or digit, such as 'maria' or 'maria.adm'."
            ),
        )
    if name in RESERVED_USERNAMES:
        raise ValidationError(
            f"The username {name!r} is reserved",
            details="It already names something in the audit log. Pick another one.",
        )
    return name


def validate_role(value: str) -> str:
    """
    Check a role name.

    Args:
        value: The role as typed.

    Returns:
        The role, lower-cased.

    Raises:
        ValidationError: When it is not one of :data:`ROLES`.
    """
    role = (value or "").strip().lower()
    if role not in ROLES:
        raise ValidationError(
            f"Unknown role: {value!r}",
            details=f"Use one of: {', '.join(ROLES)}.",
        )
    return role


def clean_display_name(value: str | None) -> str:
    """
    Trim a display name and refuse control characters.

    Args:
        value: The name as typed, or None.

    Returns:
        The trimmed name, possibly empty.

    Raises:
        ValidationError: When it is too long or carries control characters.
    """
    name = (value or "").strip()
    if len(name) > MAX_DISPLAY_NAME or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValidationError(
            "Invalid display name",
            details=f"Use at most {MAX_DISPLAY_NAME} printable characters.",
        )
    return name


def clean_person_ref(value: str | None) -> str | None:
    """
    Normalise the reference that says which person an account belongs to.

    Args:
        value: An e-mail address, an employee number, or None.

    Returns:
        The trimmed, lower-cased reference, or None when empty.

    Raises:
        ValidationError: When it is too long or carries control characters.
    """
    ref = (value or "").strip().lower()
    if not ref:
        return None
    if len(ref) > MAX_PERSON_REF or any(ord(char) < 32 or ord(char) == 127 for char in ref):
        raise ValidationError(
            "Invalid person reference",
            details=f"Use at most {MAX_PERSON_REF} printable characters, such as an e-mail.",
        )
    return ref


class AccountError(SecurityError):
    """An account operation was refused; the message says why and how to fix it."""


class AccountNotFoundError(AccountError):
    """No account has the name or id asked for."""


class AuthenticationFailed(AccountError):
    """
    A sign-in or confirmation was refused.

    The reason is for the audit log only. Whoever asked is answered the same
    way whatever it is (ENS op.acc.6.7, G10): naming it would tell a guesser
    which half of the credential was right.

    Attributes:
        reason: ``unknown_account``, ``bad_password``, ``code_required``,
            ``bad_code``, ``locked``, ``disabled``, ``invited`` or
            ``bad_invitation``.
        account_id: The account the attempt named, when there is one.
        locked_now: Whether this failure is the one that locked the account.
    """

    def __init__(
        self, reason: str, account_id: int | None = None, *, locked_now: bool = False
    ) -> None:
        """
        Args:
            reason: Why it was refused, for the audit log.
            account_id: The account the attempt named, when there is one.
            locked_now: Whether this failure locked the account.
        """
        super().__init__(
            "Invalid credentials",
            details="Check the username, the password and the code, then try again.",
        )
        self.reason = reason
        self.account_id = account_id
        self.locked_now = locked_now


@dataclass(frozen=True)
class Account:
    """
    One account, as read from the store, without any secret in it.

    Attributes:
        id: Store id; sessions and tokens refer to the account by it.
        username: Unique, lower-case sign-in name.
        display_name: How the console greets them.
        role: One of :data:`ROLES`.
        status: One of :data:`STATUSES`, as stored.
        person_ref: Who the account belongs to, for separation of duties.
        has_password: Whether a password is set.
        totp_enabled: Whether a TOTP authenticator is confirmed.
        totp_pending: Whether an enrolment has begun and is not confirmed.
        backup_codes_remaining: Unused single-use recovery codes.
        last_login_at: Last successful sign-in, UNIX seconds.
        last_login_ip: Address of that sign-in.
        failures_since_login: Refused attempts since the last success.
        last_failed_at: Last refused attempt, UNIX seconds.
        last_failed_ip: Address of that attempt.
        locked_until: End of a lockout, UNIX seconds.
        locked_reason: Why it was locked.
        notice_version: Version of the usage notice last accepted.
        notice_accepted_at: When it was accepted.
        password_changed_at: When the password was last set.
        created_at: When the account was created.
        created_by: Who created it.
        updated_at: Last change to the record.
        disabled_at: When it was disabled.
        disabled_reason: Why.
    """

    id: int
    username: str
    display_name: str
    role: str
    status: str
    person_ref: str | None
    has_password: bool
    totp_enabled: bool
    totp_pending: bool
    backup_codes_remaining: int
    last_login_at: float | None
    last_login_ip: str | None
    failures_since_login: int
    last_failed_at: float | None
    last_failed_ip: str | None
    locked_until: float | None
    locked_reason: str | None
    notice_version: str | None
    notice_accepted_at: float | None
    password_changed_at: float | None
    created_at: float
    created_by: str | None
    updated_at: float
    disabled_at: float | None
    disabled_reason: str | None
    #: Passkeys registered (:mod:`noust.core.accounts.passkeys`); last, with
    #: a default, so a record built without it still reads as before.
    passkeys: int = 0

    def is_locked(self, now: float | None = None) -> bool:
        """
        Report whether the per-account lockout is in force.

        Args:
            now: The moment to judge at; the clock by default.

        Returns:
            True while locked. A timed lock whose end has passed is over,
            even before anything rewrites the row.
        """
        if self.status != STATUS_LOCKED:
            return False
        if self.locked_until is None:
            return True
        return self.locked_until > (time.time() if now is None else now)

    def effective_status(self, now: float | None = None) -> str:
        """
        The status that applies right now.

        Args:
            now: The moment to judge at; the clock by default.

        Returns:
            The stored status, except ``active`` for a lock that has run out.
        """
        if self.status == STATUS_LOCKED and not self.is_locked(now):
            return STATUS_ACTIVE
        return self.status

    def can_sign_in(self, now: float | None = None) -> bool:
        """
        Report whether sessions and tokens of this account may be used.

        Args:
            now: The moment to judge at; the clock by default.

        Returns:
            True for an active account, including one whose lock ran out.
        """
        return self.effective_status(now) == STATUS_ACTIVE

    @property
    def has_mfa(self) -> bool:
        """
        Whether a second factor is enrolled.

        Passkeys (added in 3.1 by :mod:`noust.core.accounts.passkeys`) count
        as well; this is the one place the console asks.
        """
        return self.totp_enabled or self.passkeys > 0

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the account for the API and ``--json``, with no secret.

        Returns:
            Every public field, with the status that applies now.
        """
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "status": self.effective_status(),
            "person_ref": self.person_ref,
            "has_password": self.has_password,
            "mfa_enabled": self.has_mfa,
            "totp_enabled": self.totp_enabled,
            "passkeys": self.passkeys,
            "backup_codes_remaining": self.backup_codes_remaining,
            "last_login_at": self.last_login_at,
            "last_login_ip": self.last_login_ip,
            "failures_since_login": self.failures_since_login,
            "last_failed_at": self.last_failed_at,
            "locked_until": self.locked_until if self.is_locked() else None,
            "locked_reason": self.locked_reason if self.is_locked() else None,
            "notice_version": self.notice_version,
            "notice_accepted_at": self.notice_accepted_at,
            "password_changed_at": self.password_changed_at,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "disabled_at": self.disabled_at,
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class SodException:
    """
    A documented exception to the separation of duties for one person.

    Small installations with a single person responsible for everything are
    allowed it (art. 13.3, compensating measures), but only on the record:
    with a reason, and until a date, after which the rule applies again.

    Attributes:
        id: Store id.
        person_ref: The person it covers.
        reason: Why the exception exists.
        expires_at: When it stops applying, UNIX seconds.
        created_at: When it was recorded.
        created_by: Who recorded it.
        revoked_at: When it was withdrawn early, if it was.
    """

    id: int
    person_ref: str
    reason: str
    expires_at: float
    created_at: float
    created_by: str | None
    revoked_at: float | None

    def in_force(self, now: float | None = None) -> bool:
        """
        Args:
            now: The moment to judge at; the clock by default.

        Returns:
            True while neither revoked nor expired.
        """
        moment = time.time() if now is None else now
        return self.revoked_at is None and self.expires_at > moment

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The exception as the API and ``--json`` show it.
        """
        return {
            "id": self.id,
            "person_ref": self.person_ref,
            "reason": self.reason,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "revoked_at": self.revoked_at,
            "in_force": self.in_force(),
        }


@dataclass(frozen=True)
class LoginRecord:
    """
    What a sign-in tells the person right after it (ENS op.acc.6.r5.2).

    Attributes:
        account: The account, as it is after the sign-in was recorded.
        previous_login_at: The sign-in before this one, UNIX seconds.
        previous_login_ip: Its address.
        failures_since: Refused attempts between that sign-in and this one.
        last_failed_at: The latest of them, UNIX seconds.
        last_failed_ip: Its address.
    """

    account: Account
    previous_login_at: float | None
    previous_login_ip: str | None
    failures_since: int
    last_failed_at: float | None
    last_failed_ip: str | None
