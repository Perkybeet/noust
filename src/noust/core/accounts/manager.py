# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Accounts: the one implementation of creating, signing in and governing them.

``noust user``, the console's API and its sign-in page all call
:class:`AccountManager`; none of them writes the ``accounts`` tables itself.
That is where every rule about an account is enforced (rule 4): the password
policy, the per-account lockout, the separation of duties between roles held
by one person, single-use invitations and the second factor.

The tables are the store's (schema v12, :mod:`noust.core.schema_v12`), reached
through the store's own connection and transaction so that ``--dry-run``
rehearses an account change exactly as it rehearses any other write. The SQL
lives here, next to the rules it serves, rather than growing ``store.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from noust.core import totp
from noust.core.accounts import passwords
from noust.core.accounts.model import (
    ROLE_ADMIN,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_INVITED,
    STATUS_LOCKED,
    Account,
    AccountError,
    AccountNotFoundError,
    AuthenticationFailed,
    LoginRecord,
    SodException,
    clean_display_name,
    clean_person_ref,
    incompatible_roles,
    validate_role,
    validate_username,
)
from noust.core.accounts.policy import AuthPolicy, load_policy
from noust.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: Prefix of an invitation code, so one pasted into the wrong field is
#: recognisable at a glance.
INVITATION_PREFIX = "noust_inv_"
#: How long an invitation lasts unless the caller says otherwise.
DEFAULT_INVITATION_HOURS = 24
MAX_INVITATION_HOURS = 24 * 14

#: Single-use recovery codes issued with an authenticator. Ten base32
#: characters each (50 bits): unlike the console's shared codes they live in
#: the store, so they have to stand up to a copy of it being attacked offline.
BACKUP_CODE_COUNT = 8
_BACKUP_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"

#: What a second factor may be spent on. A TOTP code is remembered per
#: purpose, so it cannot be replayed for the same purpose (RFC 6238, 5.2).
PURPOSES = ("login", "elevate", "invitation")

#: Shortest reason, and longest life, of a separation-of-duties exception.
MIN_EXCEPTION_REASON = 10
MAX_EXCEPTION_DAYS = 366

_COLUMNS = (
    "id, username, display_name, role, status, person_ref, password_hash, "
    "password_changed_at, totp_secret, totp_pending_secret, backup_codes, "
    "last_login_at, last_login_ip, failures_since_login, last_failed_at, last_failed_ip, "
    "locked_until, locked_reason, notice_version, notice_accepted_at, created_at, "
    "created_by, updated_at, disabled_at, disabled_reason, "
    # Passkeys count as a second factor (noust.core.accounts.passkeys).
    "(SELECT COUNT(*) FROM account_passkeys WHERE account_passkeys.account_id = accounts.id) "
    "AS passkey_count"
)


def _decode_list(raw: Any) -> list[str]:
    """
    Args:
        raw: A JSON list as stored, or anything else.

    Returns:
        Its string items; empty for anything that is not a list.
    """
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _decode_steps(raw: Any) -> dict[str, int]:
    """
    Args:
        raw: The JSON object of spent TOTP steps, per purpose.

    Returns:
        The purposes mapped to their last spent step.
    """
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): int(step) for key, step in value.items() if isinstance(step, int)}


def _account(row: sqlite3.Row) -> Account:
    """
    Turn a stored row into an :class:`Account`, dropping every secret.

    Args:
        row: A row selected with :data:`_COLUMNS`.

    Returns:
        The account.
    """
    return Account(
        id=int(row["id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"] or ""),
        role=str(row["role"]),
        status=str(row["status"]),
        person_ref=row["person_ref"],
        has_password=bool(row["password_hash"]),
        totp_enabled=bool(row["totp_secret"]),
        totp_pending=bool(row["totp_pending_secret"]),
        backup_codes_remaining=len(_decode_list(row["backup_codes"])),
        last_login_at=row["last_login_at"],
        last_login_ip=row["last_login_ip"],
        failures_since_login=int(row["failures_since_login"] or 0),
        last_failed_at=row["last_failed_at"],
        last_failed_ip=row["last_failed_ip"],
        locked_until=row["locked_until"],
        locked_reason=row["locked_reason"],
        notice_version=row["notice_version"],
        notice_accepted_at=row["notice_accepted_at"],
        password_changed_at=row["password_changed_at"],
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        updated_at=float(row["updated_at"]),
        disabled_at=row["disabled_at"],
        disabled_reason=row["disabled_reason"],
        passkeys=int(row["passkey_count"] or 0),
    )


def _hash_invitation(code: str) -> str:
    """
    Args:
        code: An invitation code. 256 bits of randomness, so a plain digest
            is as good as a slow one.

    Returns:
        Its SHA-256, the only form stored.
    """
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _normalise_backup_code(code: str) -> str:
    """
    Args:
        code: A backup code as typed, with or without its dash.

    Returns:
        The code reduced to its characters.
    """
    return code.strip().lower().replace("-", "").replace(" ", "")


def _hash_backup_code(code: str, salt: str) -> str:
    """
    Args:
        code: A normalised backup code.
        salt: The code's own random salt.

    Returns:
        ``salt$digest``, the stored form.
    """
    digest = hashlib.sha256((salt + code).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def _new_backup_codes() -> tuple[list[str], list[str]]:
    """
    Returns:
        The codes in clear, to show once, and their stored forms.
    """
    shown: list[str] = []
    stored: list[str] = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10))
        shown.append(f"{raw[:5]}-{raw[5:]}")
        stored.append(_hash_backup_code(raw, secrets.token_hex(8)))
    return shown, stored


class AccountManager:
    """
    Create, authenticate and govern accounts.

    Args:
        store: The store; the process-wide one by default.
        policy: The sign-in policy; read from the configuration on every use
            by default, so a change applies without a restart.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        *,
        policy: AuthPolicy | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock or time.time

    # ------------------------------------------------------------ plumbing

    @property
    def store(self) -> NoustStore:
        """The store the accounts live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def policy(self) -> AuthPolicy:
        """
        Returns:
            The sign-in policy in force.
        """
        return self._policy or load_policy()

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """
        Run a read on the store's connection.

        Args:
            sql: A SELECT.
            params: Its parameters.

        Returns:
            The rows.
        """
        # The store owns its connection, its WAL and busy settings, and the
        # rehearsal semantics of --dry-run; going through it keeps them.
        cursor = self.store._get_connection().execute(sql, params)
        return list(cursor.fetchall())

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Cursor]:
        """
        Yields:
            A cursor inside one store transaction, rolled back on error and
            under ``--dry-run``.
        """
        with self.store._transaction() as cursor:
            yield cursor

    @contextmanager
    def _write_exclusive(self) -> Iterator[sqlite3.Cursor]:
        """
        Yields:
            A cursor inside a store transaction that already holds the write
            lock, so what is read in it cannot change before it is written.

        A secret that is spent (a backup code, a TOTP step) is read, found
        unused and then marked: with the lock taken only at the write, two
        requests with the same code both read it unused, and both are let in.
        ``BEGIN IMMEDIATE`` makes the second wait for the first to commit
        (the store's busy timeout) and read what it left.
        """
        with self._write() as cursor:
            if not cursor.connection.in_transaction:
                cursor.execute("BEGIN IMMEDIATE")
            yield cursor

    def _row(self, where: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
        """
        Args:
            where: A WHERE clause over ``accounts``.
            params: Its parameters.

        Returns:
            The first matching row, secrets included, or None.
        """
        rows = self._rows(f"SELECT {_COLUMNS}, totp_last_steps FROM accounts WHERE {where}", params)  # noqa: S608 - fixed clauses
        return rows[0] if rows else None

    def _require(self, username: str) -> sqlite3.Row:
        """
        Args:
            username: The account's name.

        Returns:
            Its row.

        Raises:
            AccountNotFoundError: When no account has that name.
        """
        row = self._row("username = ?", ((username or "").strip().lower(),))
        if row is None:
            raise AccountNotFoundError(
                f"No account named {username!r}",
                details="List the accounts with 'noust user list'.",
            )
        return row

    # ------------------------------------------------------------- queries

    def count(self) -> int:
        """
        Returns:
            How many accounts exist, in any state.
        """
        return int(self._rows("SELECT COUNT(*) AS n FROM accounts")[0]["n"])

    def any_exist(self) -> bool:
        """
        Returns:
            Whether any account exists. While none does, Noust behaves as 3.0
            did: the master token is the one credential.
        """
        return bool(self._rows("SELECT 1 FROM accounts LIMIT 1"))

    def get(self, account_id: int) -> Account | None:
        """
        Args:
            account_id: The account's id.

        Returns:
            The account, or None.
        """
        row = self._row("id = ?", (int(account_id),))
        return _account(row) if row is not None else None

    def find(self, username: str) -> Account | None:
        """
        Args:
            username: The account's name, in any case.

        Returns:
            The account, or None.
        """
        row = self._row("username = ?", ((username or "").strip().lower(),))
        return _account(row) if row is not None else None

    def require(self, username: str) -> Account:
        """
        Args:
            username: The account's name.

        Returns:
            The account.

        Raises:
            AccountNotFoundError: When no account has that name.
        """
        return _account(self._require(username))

    def require_id(self, account_id: int) -> Account:
        """
        Args:
            account_id: The account's id.

        Returns:
            The account.

        Raises:
            AccountNotFoundError: When no account has that id.
        """
        account = self.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"No account with id {account_id}")
        return account

    def list_all(self) -> list[Account]:
        """
        Returns:
            Every account, by username.
        """
        rows = self._rows(f"SELECT {_COLUMNS} FROM accounts ORDER BY username")  # noqa: S608 - fixed columns
        return [_account(row) for row in rows]

    def first_admin(self) -> Account | None:
        """
        Returns:
            The oldest ``admin`` account that is not disabled or merely
            invited: the owner tokens issued before accounts existed are
            handed to.
        """
        row = self._row(
            "role = ? AND status IN (?, ?) ORDER BY created_at, id LIMIT 1",
            (ROLE_ADMIN, STATUS_ACTIVE, STATUS_LOCKED),
        )
        return _account(row) if row is not None else None

    # ----------------------------------------------------------- lifecycle

    def create(
        self,
        username: str,
        role: str,
        *,
        password: str | None,
        display_name: str | None = None,
        person_ref: str | None = None,
        created_by: str | None = None,
    ) -> Account:
        """
        Create an active account with a password.

        The second factor is enrolled at its first sign-in, before the
        session may do anything else.

        Args:
            username: Sign-in name.
            role: One of :data:`noust.core.accounts.model.ROLES`.
            password: The password; checked against the policy.
            display_name: How the console greets them.
            person_ref: Who the account belongs to, for separation of duties.
            created_by: Who is creating it, for the record.

        Returns:
            The account.

        Raises:
            ValidationError: When a field or the password is refused.
            AccountError: When the name is taken or the role is incompatible
                with another account of the same person.
        """
        name = validate_username(username)
        if not password:
            raise ValidationError(
                "A password is required",
                details="Type it at the prompt, or pipe it with --stdin; never in the command line.",
            )
        policy = self.policy()
        passwords.check_password_policy(
            password, username=name, min_length=policy.password_min_length
        )
        return self._insert(
            name,
            validate_role(role),
            status=STATUS_ACTIVE,
            password_hash=passwords.hash_password(password),
            display_name=clean_display_name(display_name),
            person_ref=clean_person_ref(person_ref),
            created_by=created_by,
        )

    def _insert(
        self,
        username: str,
        role: str,
        *,
        status: str,
        password_hash: str | None,
        display_name: str,
        person_ref: str | None,
        created_by: str | None,
    ) -> Account:
        """
        Write a new account row after the separation-of-duties check.

        Args:
            username: Validated name.
            role: Validated role.
            status: Initial status.
            password_hash: Stored hash, or None for an invitation.
            display_name: Cleaned display name.
            person_ref: Cleaned person reference.
            created_by: Who is creating it.

        Returns:
            The account.

        Raises:
            AccountError: When the name is taken or the roles clash.
        """
        self._check_separation(person_ref, role, exclude_id=None)
        now = self.now()
        try:
            with self._write() as cursor:
                cursor.execute(
                    "INSERT INTO accounts (username, display_name, role, status, person_ref, "
                    "password_hash, password_changed_at, created_at, created_by, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        username,
                        display_name,
                        role,
                        status,
                        person_ref,
                        password_hash,
                        now if password_hash else None,
                        now,
                        created_by,
                        now,
                    ),
                )
                account_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise AccountError(
                f"An account named {username!r} already exists",
                details="Pick another name, or manage that account with 'noust user'.",
            ) from exc
        account = self.get(account_id)
        if account is None:
            # Under --dry-run the insert was rolled back; describe what it
            # would have been rather than pretend it does not exist.
            return Account(
                id=account_id,
                username=username,
                display_name=display_name,
                role=role,
                status=status,
                person_ref=person_ref,
                has_password=bool(password_hash),
                totp_enabled=False,
                totp_pending=False,
                backup_codes_remaining=0,
                last_login_at=None,
                last_login_ip=None,
                failures_since_login=0,
                last_failed_at=None,
                last_failed_ip=None,
                locked_until=None,
                locked_reason=None,
                notice_version=None,
                notice_accepted_at=None,
                password_changed_at=now if password_hash else None,
                created_at=now,
                created_by=created_by,
                updated_at=now,
                disabled_at=None,
                disabled_reason=None,
            )
        return account

    def invite(
        self,
        username: str,
        role: str | None = None,
        *,
        display_name: str | None = None,
        person_ref: str | None = None,
        created_by: str | None = None,
        expires_hours: float = DEFAULT_INVITATION_HOURS,
    ) -> tuple[Account, str]:
        """
        Issue a single-use invitation: the person sets their own password.

        For a new name, the account is created ``invited`` with ``role``. For
        an existing account, the invitation is how its owner recovers: it
        replaces the password and the authenticator when accepted, and the
        account keeps working until then. Either way the code is shown once;
        only its digest is stored, and every earlier unused invitation of the
        account stops working.

        Args:
            username: The account's name.
            role: The role of a new account; ignored for an existing one.
            display_name: Display name of a new account.
            person_ref: Person reference of a new account.
            created_by: Who is inviting, for the record.
            expires_hours: Life of the invitation, at most two weeks.

        Returns:
            The account and the invitation code, in clear, exactly once.

        Raises:
            ValidationError: When a field is refused.
            AccountError: When a new account's role clashes, or the existing
                account is disabled.
        """
        if not 0 < expires_hours <= MAX_INVITATION_HOURS:
            raise ValidationError(
                "An invitation lasts between one hour and two weeks",
                details=f"Pass --expires-hours between 1 and {MAX_INVITATION_HOURS}.",
            )
        name = validate_username(username)
        existing = self.find(name)
        if existing is None:
            if role is None:
                raise ValidationError(
                    "A role is required to invite a new account",
                    details="Pass --role viewer, operator, admin, security or auditor.",
                )
            account = self._insert(
                name,
                validate_role(role),
                status=STATUS_INVITED,
                password_hash=None,
                display_name=clean_display_name(display_name),
                person_ref=clean_person_ref(person_ref),
                created_by=created_by,
            )
        elif existing.status == STATUS_DISABLED:
            raise AccountError(
                f"The account {name!r} is disabled",
                details=f"Enable it first with 'noust user enable {name}'.",
            )
        else:
            # A recovery hands the account, role and all, to whoever holds the
            # code: the role is granted again, so it is held to the same
            # separation of duties as granting it (an exception may have ended).
            self._check_separation(existing.person_ref, existing.role, exclude_id=existing.id)
            account = existing

        code = f"{INVITATION_PREFIX}{secrets.token_urlsafe(32)}"
        now = self.now()
        with self._write() as cursor:
            cursor.execute(
                "UPDATE account_invitations SET used_at = ? WHERE account_id = ? AND used_at IS NULL",
                (now, account.id),
            )
            cursor.execute(
                "INSERT INTO account_invitations (account_id, code_hash, expires_at, created_at, "
                "created_by) VALUES (?, ?, ?, ?, ?)",
                (account.id, _hash_invitation(code), now + expires_hours * 3600, now, created_by),
            )
        return account, code

    def _invitation(self, code: str) -> tuple[sqlite3.Row, sqlite3.Row]:
        """
        Find the live invitation a code names, and its account.

        Args:
            code: The code as pasted.

        Returns:
            The invitation row and the account row.

        Raises:
            AuthenticationFailed: When the code is unknown, used, expired, or
                its account is disabled - one answer for all four.
        """
        rows = self._rows(
            "SELECT * FROM account_invitations WHERE code_hash = ? AND used_at IS NULL "
            "AND expires_at > ?",
            (_hash_invitation(code or ""), self.now()),
        )
        if not rows:
            raise AuthenticationFailed("bad_invitation")
        invitation = rows[0]
        account = self._row("id = ?", (int(invitation["account_id"]),))
        if account is None or account["status"] == STATUS_DISABLED:
            raise AuthenticationFailed("bad_invitation")
        return invitation, account

    def open_invitation(self, code: str) -> tuple[Account, str]:
        """
        Start accepting an invitation: hand out the authenticator secret to enrol.

        Opening it twice returns the same secret, so reloading the page does
        not invalidate the QR code already scanned.

        Args:
            code: The invitation code.

        Returns:
            The account and the pending TOTP secret.

        Raises:
            AuthenticationFailed: When the code is not a live invitation.
        """
        _invitation, row = self._invitation(code)
        current = self._open_totp(int(row["id"]), row["totp_pending_secret"])
        secret = current or totp.generate_secret()
        if secret != current or not totp.is_sealed(row["totp_pending_secret"]):
            with self._write() as cursor:
                cursor.execute(
                    "UPDATE accounts SET totp_pending_secret = ?, updated_at = ? WHERE id = ?",
                    (self._seal_totp(int(row["id"]), secret), self.now(), int(row["id"])),
                )
        return _account(row), secret

    def accept_invitation(
        self, code: str, password: str, totp_code: str, *, notice_version: str | None = None
    ) -> tuple[Account, list[str]]:
        """
        Finish an invitation: set the password and the authenticator.

        Args:
            code: The invitation code.
            password: The password the person chose.
            totp_code: A current code from the authenticator they enrolled
                with the secret :meth:`open_invitation` returned.
            notice_version: The version of the usage notice they accepted, if
                one is configured.

        Returns:
            The active account and its backup codes, in clear, exactly once.

        Raises:
            AuthenticationFailed: When the code is not a live invitation.
            ValidationError: When the password is refused or the TOTP code
                does not match; the invitation stays usable.
        """
        invitation, row = self._invitation(code)
        policy = self.policy()
        passwords.check_password_policy(
            password, username=str(row["username"]), min_length=policy.password_min_length
        )
        pending = self._open_totp(int(row["id"]), row["totp_pending_secret"])
        if not pending:
            raise ValidationError(
                "The authenticator has not been set up yet",
                details="Open the invitation first; it shows the code to scan.",
            )
        step = totp.matched_step(pending, totp_code or "", t=self.now())
        if step is None:
            raise ValidationError(
                "That authenticator code was not accepted",
                details="Scan the QR code again and type the code the app shows now.",
            )
        if notice_version is not None and notice_version != policy.notice_version:
            raise ValidationError(
                "The usage notice changed while you were reading it",
                details="Reload the invitation and accept the current notice.",
            )
        shown, stored = _new_backup_codes()
        now = self.now()
        status = (
            STATUS_ACTIVE if row["status"] in (STATUS_INVITED, STATUS_LOCKED) else row["status"]
        )
        with self._write() as cursor:
            cursor.execute(
                "UPDATE accounts SET password_hash = ?, password_changed_at = ?, totp_secret = ?, "
                "totp_pending_secret = NULL, totp_last_steps = ?, backup_codes = ?, status = ?, "
                "consecutive_failures = 0, locked_until = NULL, locked_reason = NULL, "
                "notice_version = COALESCE(?, notice_version), "
                "notice_accepted_at = CASE WHEN ? IS NULL THEN notice_accepted_at ELSE ? END, "
                "updated_at = ? WHERE id = ?",
                (
                    passwords.hash_password(password),
                    now,
                    self._seal_totp(int(row["id"]), pending),
                    json.dumps({"invitation": step}),
                    json.dumps(stored),
                    status,
                    notice_version,
                    notice_version,
                    now,
                    now,
                    int(row["id"]),
                ),
            )
            cursor.execute(
                "UPDATE account_invitations SET used_at = ? WHERE id = ?",
                (now, int(invitation["id"])),
            )
        return self.require(str(row["username"])), shown

    def set_role(self, username: str, role: str) -> Account:
        """
        Change an account's role.

        Args:
            username: The account's name.
            role: The new role.

        Returns:
            The account after the change.

        Raises:
            AccountNotFoundError: When no account has that name.
            AccountError: When the role clashes with another account of the
                same person.
        """
        row = self._require(username)
        new_role = validate_role(role)
        self._check_separation(row["person_ref"], new_role, exclude_id=int(row["id"]))
        self._update(int(row["id"]), role=new_role)
        return self.require(username)

    def update_profile(
        self, username: str, *, display_name: str | None = None, person_ref: str | None = None
    ) -> Account:
        """
        Change an account's display name or person reference.

        Args:
            username: The account's name.
            display_name: The new display name, or None to keep it.
            person_ref: The new person reference, ``""`` to clear it, or None
                to keep it.

        Returns:
            The account after the change.

        Raises:
            AccountNotFoundError: When no account has that name.
            AccountError: When the new person already holds an incompatible role.
        """
        row = self._require(username)
        changes: dict[str, Any] = {}
        if display_name is not None:
            changes["display_name"] = clean_display_name(display_name)
        if person_ref is not None:
            ref = clean_person_ref(person_ref)
            self._check_separation(ref, str(row["role"]), exclude_id=int(row["id"]))
            changes["person_ref"] = ref
        if changes:
            self._update(int(row["id"]), **changes)
        return self.require(username)

    def disable(self, username: str, reason: str | None = None) -> Account:
        """
        Disable an account: its sessions and tokens stop working at once.

        Accounts are disabled rather than deleted so the audit trail keeps
        naming somebody (ENS op.acc.1.4).

        Args:
            username: The account's name.
            reason: Why, for the record.

        Returns:
            The account after the change.

        Raises:
            AccountNotFoundError: When no account has that name.
        """
        row = self._require(username)
        self._update(
            int(row["id"]),
            status=STATUS_DISABLED,
            disabled_at=self.now(),
            disabled_reason=(reason or "").strip()[:500] or None,
        )
        return self.require(username)

    def enable(self, username: str) -> Account:
        """
        Enable a disabled account again.

        Args:
            username: The account's name.

        Returns:
            The account after the change: ``invited`` when it never set a
            password, ``active`` otherwise.

        Raises:
            AccountNotFoundError: When no account has that name.
            AccountError: When re-enabling it would break separation of duties.
        """
        row = self._require(username)
        if row["status"] != STATUS_DISABLED:
            return _account(row)
        self._check_separation(row["person_ref"], str(row["role"]), exclude_id=int(row["id"]))
        self._update(
            int(row["id"]),
            status=STATUS_ACTIVE if row["password_hash"] else STATUS_INVITED,
            disabled_at=None,
            disabled_reason=None,
            consecutive_failures=0,
        )
        return self.require(username)

    def unlock(self, username: str) -> Account:
        """
        Lift a per-account lockout before it runs out.

        Args:
            username: The account's name.

        Returns:
            The account after the change.

        Raises:
            AccountNotFoundError: When no account has that name.
        """
        row = self._require(username)
        changes: dict[str, Any] = {
            "consecutive_failures": 0,
            "locked_until": None,
            "locked_reason": None,
        }
        if row["status"] == STATUS_LOCKED:
            changes["status"] = STATUS_ACTIVE
        self._update(int(row["id"]), **changes)
        return self.require(username)

    def reset_mfa(self, username: str) -> Account:
        """
        Remove an account's authenticator, passkeys and backup codes.

        Its next sign-in has to enrol a new one before doing anything else.

        Args:
            username: The account's name.

        Returns:
            The account after the change.

        Raises:
            AccountNotFoundError: When no account has that name.
        """
        row = self._require(username)
        self._update(
            int(row["id"]),
            totp_secret=None,
            totp_pending_secret=None,
            totp_last_steps="{}",
            backup_codes="[]",
        )
        # Passkeys are second factors too: a reset that left them would
        # leave the lost or stolen device still able to sign in.
        with self._write() as cursor:
            cursor.execute("DELETE FROM account_passkeys WHERE account_id = ?", (int(row["id"]),))
        return self.require(username)

    def set_password(self, username: str, password: str) -> Account:
        """
        Set an account's password, from the root CLI.

        Args:
            username: The account's name.
            password: The new password; checked against the policy.

        Returns:
            The account after the change.

        Raises:
            AccountNotFoundError: When no account has that name.
            ValidationError: When the password is refused.
        """
        row = self._require(username)
        passwords.check_password_policy(
            password, username=str(row["username"]), min_length=self.policy().password_min_length
        )
        changes: dict[str, Any] = {
            "password_hash": passwords.hash_password(password),
            "password_changed_at": self.now(),
        }
        if row["status"] == STATUS_INVITED:
            changes["status"] = STATUS_ACTIVE
        self._update(int(row["id"]), **changes)
        return self.require(username)

    def change_password(
        self, account_id: int, current: str, new: str, *, client_ip: str
    ) -> Account:
        """
        Let an account holder change their own password.

        Args:
            account_id: The account.
            current: The password in force; a wrong one counts as a failure.
            new: The new password; checked against the policy.
            client_ip: Where the request came from, for the failure record.

        Returns:
            The account after the change.

        Raises:
            AuthenticationFailed: When ``current`` is wrong.
            ValidationError: When ``new`` is refused.
        """
        row = self._row("id = ?", (int(account_id),))
        if row is None:
            raise AuthenticationFailed("unknown_account")
        if not passwords.verify_password(current, row["password_hash"]):
            self.record_failure(int(row["id"]), client_ip)
            raise AuthenticationFailed("bad_password", int(row["id"]))
        passwords.check_password_policy(
            new, username=str(row["username"]), min_length=self.policy().password_min_length
        )
        self._update(
            int(row["id"]),
            password_hash=passwords.hash_password(new),
            password_changed_at=self.now(),
        )
        return self.require(str(row["username"]))

    def remove(self, username: str) -> Account:
        """
        Delete an account and its invitations.

        Prefer :meth:`disable`: a removed account's name in the audit log no
        longer names a row. Removal is for a mistake, such as an invitation
        sent to the wrong name.

        Args:
            username: The account's name.

        Returns:
            The account as it was.

        Raises:
            AccountNotFoundError: When no account has that name.
        """
        row = self._require(username)
        with self._write() as cursor:
            cursor.execute(
                "DELETE FROM account_invitations WHERE account_id = ?", (int(row["id"]),)
            )
            cursor.execute("DELETE FROM accounts WHERE id = ?", (int(row["id"]),))
        return _account(row)

    def _update(self, account_id: int, **changes: Any) -> None:
        """
        Write some columns of one account.

        Args:
            account_id: The account.
            **changes: Column to value; the names are this module's own.
        """
        changes["updated_at"] = self.now()
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with self._write() as cursor:
            cursor.execute(
                f"UPDATE accounts SET {assignments} WHERE id = ?",  # noqa: S608 - own column names
                (*changes.values(), account_id),
            )

    # ------------------------------------------ TOTP secrets at rest (ENS G22)

    def _totp_key(self, *, create: bool) -> bytes | None:
        """
        The key the TOTP secrets are sealed under, beside the store.

        Args:
            create: Create it when there is none yet.

        Returns:
            The key, or None when there is none and ``create`` is false.
        """
        from noust.core.fs import DryRunFileSystem, get_fs

        path = totp.key_path(self.store.db_path)
        if create and isinstance(get_fs(), DryRunFileSystem):
            # A rehearsal creates no file; its store writes are rolled back.
            return totp.load_key(path, create=False) or secrets.token_bytes(32)
        return totp.load_key(path, create=create)

    def _seal_totp(self, account_id: int, secret: str) -> str:
        """
        Args:
            account_id: The account the secret belongs to.
            secret: The secret, base32.

        Returns:
            What the store keeps: the secret encrypted and bound to the account.
        """
        key = self._totp_key(create=True)
        if key is None:  # create=True yields a key or raises; kept for the type
            raise AccountError("The TOTP key could not be created beside the store")
        return totp.seal_secret(secret, key, f"account:{int(account_id)}")

    def _open_totp(self, account_id: int, stored: Any) -> str | None:
        """
        Args:
            account_id: The account the secret belongs to.
            stored: The column's value; one in clear (before 3.1's encryption
                at rest) is read as it is.

        Returns:
            The secret, or None when there is none.

        Raises:
            AccountError: It cannot be opened: its key is missing or it was
                altered; the fix is in the message.
        """
        if not stored:
            return None
        text = str(stored)
        try:
            key = self._totp_key(create=False) if totp.is_sealed(text) else None
            return totp.open_secret(text, key, f"account:{int(account_id)}")
        except (totp.TotpSealError, OSError) as exc:
            raise AccountError(
                f"The second factor of account {account_id} cannot be read: {exc}",
                details="Restore the TOTP key beside the store, or reset the factor: "
                "noust user reset-mfa <user>.",
            ) from exc

    def plaintext_totp_secrets(self) -> int:
        """
        Count the TOTP secrets still stored in clear, for the ENS check.

        Returns:
            How many columns hold a secret without encryption.
        """
        rows = self._rows("SELECT totp_secret, totp_pending_secret FROM accounts")
        return sum(
            1 for row in rows for value in (row[0], row[1]) if value and not totp.is_sealed(value)
        )

    def seal_stored_totp_secrets(self) -> int:
        """
        Encrypt every TOTP secret still stored in clear.

        The one-time data migration of encryption at rest: the console runs
        it at start, and a secret met in clear before that is sealed when it
        is used. Idempotent.

        Returns:
            How many secrets were sealed.
        """
        rows = self._rows(
            "SELECT id, totp_secret, totp_pending_secret FROM accounts "
            "WHERE totp_secret IS NOT NULL OR totp_pending_secret IS NOT NULL"
        )
        sealed = 0
        for row in rows:
            changes: dict[str, Any] = {}
            for column in ("totp_secret", "totp_pending_secret"):
                value = row[column]
                if value and not totp.is_sealed(value):
                    changes[column] = self._seal_totp(int(row["id"]), str(value))
            if changes:
                self._update(int(row["id"]), **changes)
                sealed += len(changes)
        return sealed

    # ------------------------------------------------------ authentication

    def _sign_in_row(self, identifier: str) -> sqlite3.Row | None:
        """
        Find the account a sign-in names: by its username, or by its person's e-mail.

        A username never holds an ``@`` (:data:`USERNAME_PATTERN`), so a name
        with one is read as the e-mail of the person an account belongs to
        (``person_ref``). It names an account only when exactly one account of
        that person may sign in: a person with two (an ``admin`` and a
        ``security``, say) signs in with the username of the one they mean.
        A disabled account, or one only invited, is not counted.

        Args:
            identifier: What was typed in the username field.

        Returns:
            The account's row, secrets included, or None.
        """
        typed = (identifier or "").strip()
        if "@" in typed:
            try:
                ref = clean_person_ref(typed)
            except ValidationError:
                return None
            rows = self._rows(
                f"SELECT {_COLUMNS}, totp_last_steps FROM accounts "  # noqa: S608 - fixed clauses
                "WHERE person_ref = ? AND status NOT IN (?, ?) LIMIT 2",
                (ref, STATUS_DISABLED, STATUS_INVITED),
            )
            return rows[0] if len(rows) == 1 else None
        try:
            name = validate_username(typed)
        except ValidationError:
            return None
        return self._row("username = ?", (name,))

    def authenticate(
        self,
        username: str,
        password: str,
        code: str | None,
        *,
        client_ip: str,
        purpose: str = "login",
    ) -> Account:
        """
        Check a username, a password and, when enrolled, a second factor, in one go.

        What a script signing in with one request uses; the console asks for
        the password first (:meth:`authenticate_password`) and the second
        factor on its own step (:meth:`complete_second_factor`). Every refusal
        is the same :class:`AuthenticationFailed` to the caller; its
        ``reason`` is for the audit log.

        An account without a second factor is let in on its password alone:
        the session it gets may do nothing but enrol one.

        Args:
            username: The username typed, or the e-mail of the account's
                person (:meth:`_sign_in_row`).
            password: The password typed.
            code: A TOTP code or a backup code; required when the account has
                an authenticator.
            client_ip: Where the attempt came from, for the failure record.
            purpose: What the second factor is spent on: ``login`` or
                ``elevate``.

        Returns:
            The account.

        Raises:
            AuthenticationFailed: When anything does not match, the account is
                locked, disabled or only invited.
        """
        account = self.authenticate_password(username, password, client_ip=client_ip)
        if account.has_mfa:
            if not (code or "").strip():
                locked = self.record_failure(account.id, client_ip)
                raise AuthenticationFailed("code_required", account.id, locked_now=locked)
            if not self.verify_second_factor(account.id, code or "", purpose=purpose):
                locked = self.record_failure(account.id, client_ip)
                raise AuthenticationFailed("bad_code", account.id, locked_now=locked)
        return account

    def authenticate_password(self, username: str, password: str, *, client_ip: str) -> Account:
        """
        Check the first step of a sign-in: who, and their password.

        Everything but the second factor is checked here, and refused the one
        way whatever it was. The password is hashed whatever happens, against
        a dummy hash for an unknown name, so the answer takes as long for a
        name that does not exist as for one that does. A right password does
        not reset the failure counters: only a completed sign-in does
        (:meth:`record_login`).

        Args:
            username: The username typed, or the e-mail of the account's
                person (:meth:`_sign_in_row`).
            password: The password typed.
            client_ip: Where the attempt came from, for the failure record.

        Returns:
            The account; whether it still owes a second factor is its
            ``has_mfa``.

        Raises:
            AuthenticationFailed: When the name or the password does not
                match, or the account is locked, disabled or only invited.
        """
        row = self._sign_in_row(username)
        stored = row["password_hash"] if row is not None else None
        password_ok = passwords.verify_password(password or "", stored)
        if row is None:
            raise AuthenticationFailed("unknown_account")
        account = _account(row)
        self._require_usable(account, client_ip)
        if not password_ok:
            locked = self.record_failure(account.id, client_ip)
            raise AuthenticationFailed("bad_password", account.id, locked_now=locked)
        if passwords.needs_rehash(stored):
            self._update(account.id, password_hash=passwords.hash_password(password))
        return account

    def complete_second_factor(
        self, account_id: int, code: str, *, client_ip: str, purpose: str = "login"
    ) -> Account:
        """
        Check the second step of a sign-in, whose password step already passed.

        The account is read again: one disabled or locked since its password
        was checked is refused here. A wrong code counts towards the account's
        lockout exactly as a wrong password does.

        Args:
            account_id: The account the password step named.
            code: A TOTP code or a backup code.
            client_ip: Where the attempt came from, for the failure record.
            purpose: What the code is spent on.

        Returns:
            The account.

        Raises:
            AuthenticationFailed: ``bad_code`` for a wrong code (and for an
                account with no second factor to check: this fails closed),
                or the account's state when it can no longer sign in.
        """
        account = self.get(int(account_id))
        if account is None:
            raise AuthenticationFailed("unknown_account")
        self._require_usable(account, client_ip)
        if not account.has_mfa or not self.verify_second_factor(
            account.id, code or "", purpose=purpose
        ):
            locked = self.record_failure(account.id, client_ip)
            raise AuthenticationFailed("bad_code", account.id, locked_now=locked)
        return account

    def _require_usable(self, account: Account, client_ip: str) -> None:
        """
        Refuse an account that cannot sign in whatever it presents.

        Args:
            account: The account.
            client_ip: Where the attempt came from, for the record.

        Raises:
            AuthenticationFailed: ``disabled``, ``invited`` or ``locked``.
        """
        if account.status == STATUS_DISABLED:
            self._note_attempt(account.id, client_ip)
            raise AuthenticationFailed("disabled", account.id)
        if account.status == STATUS_INVITED:
            raise AuthenticationFailed("invited", account.id)
        if account.is_locked(self.now()):
            self._note_attempt(account.id, client_ip)
            raise AuthenticationFailed("locked", account.id)

    def verify_password(self, account_id: int, password: str, *, client_ip: str) -> bool:
        """
        Check an account's password alone, counting a wrong one.

        Args:
            account_id: The account.
            password: What was typed.
            client_ip: Where it came from.

        Returns:
            True when it matches an account that may sign in.
        """
        row = self._row("id = ?", (int(account_id),))
        ok = passwords.verify_password(password or "", row["password_hash"] if row else None)
        if row is None:
            return False
        if not ok:
            self.record_failure(int(row["id"]), client_ip)
            return False
        return _account(row).can_sign_in(self.now())

    def _note_attempt(self, account_id: int, client_ip: str) -> None:
        """
        Record an attempt on an account that could not have succeeded.

        Args:
            account_id: The account.
            client_ip: Where it came from.
        """
        with self._write() as cursor:
            cursor.execute(
                "UPDATE accounts SET failures_since_login = failures_since_login + 1, "
                "last_failed_at = ?, last_failed_ip = ? WHERE id = ?",
                (self.now(), client_ip, account_id),
            )

    def record_failure(self, account_id: int, client_ip: str) -> bool:
        """
        Count a refused attempt, locking the account at the threshold.

        This is the per-account lockout (ENS op.acc.6.8), on top of the
        per-address one the console already has: an attacker rotating
        addresses still runs out of attempts on the account.

        Args:
            account_id: The account.
            client_ip: Where the attempt came from.

        Returns:
            True when this failure locked the account.
        """
        policy = self.policy()
        now = self.now()
        with self._write() as cursor:
            cursor.execute(
                "UPDATE accounts SET failures_since_login = failures_since_login + 1, "
                "consecutive_failures = consecutive_failures + 1, last_failed_at = ?, "
                "last_failed_ip = ? WHERE id = ?",
                (now, client_ip, account_id),
            )
            row = cursor.execute(
                "SELECT consecutive_failures, status FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if row is None or int(row[0]) < policy.lockout_threshold:
                return False
            if row[1] not in (STATUS_ACTIVE, STATUS_LOCKED):
                return False
            cursor.execute(
                "UPDATE accounts SET status = ?, locked_until = ?, locked_reason = ?, "
                "consecutive_failures = 0, updated_at = ? WHERE id = ?",
                (
                    STATUS_LOCKED,
                    now + policy.lockout_minutes * 60,
                    "too_many_failures",
                    now,
                    account_id,
                ),
            )
        return True

    def record_login(self, account_id: int, client_ip: str) -> LoginRecord:
        """
        Record a successful sign-in, and say what happened since the last one.

        Args:
            account_id: The account.
            client_ip: Where the sign-in came from.

        Returns:
            The previous sign-in and the failures since, to show the person.

        Raises:
            AccountNotFoundError: When the account vanished meanwhile.
        """
        row = self._row("id = ?", (int(account_id),))
        if row is None:
            raise AccountNotFoundError(f"No account with id {account_id}")
        now = self.now()
        with self._write() as cursor:
            cursor.execute(
                "UPDATE accounts SET last_login_at = ?, last_login_ip = ?, "
                "failures_since_login = 0, consecutive_failures = 0, locked_until = NULL, "
                "locked_reason = NULL, status = CASE WHEN status = ? THEN ? ELSE status END, "
                "updated_at = ? WHERE id = ?",
                (now, client_ip, STATUS_LOCKED, STATUS_ACTIVE, now, int(account_id)),
            )
        account = self.get(int(account_id)) or _account(row)
        return LoginRecord(
            account=account,
            previous_login_at=row["last_login_at"],
            previous_login_ip=row["last_login_ip"],
            failures_since=int(row["failures_since_login"] or 0),
            last_failed_at=row["last_failed_at"],
            last_failed_ip=row["last_failed_ip"],
        )

    # -------------------------------------------------------- second factor

    def verify_second_factor(self, account_id: int, code: str, *, purpose: str) -> bool:
        """
        Check a TOTP code or spend a backup code of one account.

        Args:
            account_id: The account.
            code: What was typed.
            purpose: What it is spent on; a TOTP step is not accepted twice
                for the same purpose. Backup codes are single-use whatever
                the purpose.

        Returns:
            True when it matched; False otherwise, including when the account
            has no authenticator (this fails closed). The reading and the
            spending are one locked step: the same code presented twice at
            once is accepted once.
        """
        if not (code or "").strip():
            return False
        with self._write_exclusive() as cursor:
            row = cursor.execute(
                "SELECT totp_secret, totp_last_steps, backup_codes FROM accounts WHERE id = ?",
                (int(account_id),),
            ).fetchone()
            if row is None:
                return False
            # A passkey-only account has backup codes and no authenticator:
            # the codes are checked whichever factor they came with.
            try:
                secret = self._open_totp(int(account_id), row[0])
            except AccountError as exc:
                # Fails closed, loudly: a missing key must not read as a typo.
                logger.error("%s", exc)
                secret = None
            step = totp.matched_step(secret, code, t=self.now()) if secret else None
            if step is not None:
                spent = _decode_steps(row[1])
                last = spent.get(purpose)
                if last is not None and step <= last:
                    return False
                spent[purpose] = step
                sealed = (
                    row[0] if totp.is_sealed(row[0]) else self._seal_totp(account_id, secret or "")
                )
                cursor.execute(
                    "UPDATE accounts SET totp_last_steps = ?, totp_secret = ? WHERE id = ?",
                    (json.dumps(spent), sealed, int(account_id)),
                )
                return True
            presented = _normalise_backup_code(code)
            stored = _decode_list(row[2])
            remaining = []
            matched = False
            for entry in stored:
                salt, _, _digest = entry.partition("$")
                if not matched and hmac.compare_digest(entry, _hash_backup_code(presented, salt)):
                    matched = True
                    continue
                remaining.append(entry)
            if matched:
                cursor.execute(
                    "UPDATE accounts SET backup_codes = ? WHERE id = ?",
                    (json.dumps(remaining), int(account_id)),
                )
            return matched

    def begin_totp(self, account_id: int) -> str:
        """
        Generate the secret for an account's first authenticator.

        Args:
            account_id: The account.

        Returns:
            The pending secret, to show once as a QR code and as text.

        Raises:
            AccountError: When an authenticator is already enrolled: replacing
                it takes ``noust user reset-mfa`` or an invitation, so a
                hijacked session cannot swap it for its own.
        """
        row = self._row("id = ?", (int(account_id),))
        if row is None:
            raise AccountNotFoundError(f"No account with id {account_id}")
        if row["totp_secret"]:
            raise AccountError(
                "This account already has an authenticator",
                details="Ask a security officer to reset it (noust user reset-mfa).",
            )
        secret = self._open_totp(int(account_id), row["totp_pending_secret"]) or (
            totp.generate_secret()
        )
        self._update(int(account_id), totp_pending_secret=self._seal_totp(int(account_id), secret))
        return str(secret)

    def pending_totp_secret(self, account_id: int) -> str | None:
        """
        Args:
            account_id: The account.

        Returns:
            The secret of an enrolment begun and not confirmed, or None.
        """
        row = self._row("id = ?", (int(account_id),))
        return self._open_totp(int(account_id), row["totp_pending_secret"]) if row else None

    def confirm_totp(self, account_id: int, code: str) -> list[str] | None:
        """
        Activate the pending authenticator on a code from it.

        Args:
            account_id: The account.
            code: A current code for the pending secret.

        Returns:
            The backup codes, in clear, exactly once; None when the code did
            not match.

        Raises:
            AccountError: When no enrolment was begun.
        """
        row = self._row("id = ?", (int(account_id),))
        if row is None or not row["totp_pending_secret"]:
            raise AccountError(
                "No authenticator enrolment is in progress",
                details="Begin one first: POST /api/auth/2fa/enroll.",
            )
        pending = self._open_totp(int(account_id), row["totp_pending_secret"]) or ""
        step = totp.matched_step(pending, code or "", t=self.now())
        if step is None:
            return None
        shown, stored = _new_backup_codes()
        self._update(
            int(account_id),
            totp_secret=self._seal_totp(int(account_id), pending),
            totp_pending_secret=None,
            totp_last_steps=json.dumps({"login": step, "elevate": step}),
            backup_codes=json.dumps(stored),
        )
        return shown

    def regenerate_backup_codes(self, account_id: int) -> list[str]:
        """
        Replace an account's backup codes, retiring the old ones.

        Args:
            account_id: The account.

        Returns:
            The new codes, in clear, exactly once.

        Raises:
            AccountError: When the account has no authenticator.
        """
        account = self.get(account_id)
        if account is None or not account.has_mfa:
            raise AccountError(
                "This account has no second factor",
                details="Enrol an authenticator or a passkey first; backup codes come with it.",
            )
        shown, stored = _new_backup_codes()
        self._update(int(account_id), backup_codes=json.dumps(stored))
        return shown

    # --------------------------------------------------------------- notice

    def accept_notice(self, account_id: int, version: str) -> Account:
        """
        Record that an account holder accepted the usage notice.

        Args:
            account_id: The account.
            version: The version they were shown.

        Returns:
            The account after the change.

        Raises:
            ValidationError: When the version is not the one in force.
        """
        current = self.policy().notice_version
        if current is None or version != current:
            raise ValidationError(
                "That is not the usage notice in force",
                details="Reload the page to read the current notice, then accept it.",
            )
        self._update(int(account_id), notice_version=version, notice_accepted_at=self.now())
        account = self.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"No account with id {account_id}")
        return account

    # ------------------------------------------------ separation of duties

    def check_grant(self, account: Account, by: Account | None, action: str) -> None:
        """
        Refuse handing an account's role to the person who asks for it.

        A recovery invitation (and a role change) gives an account's role to
        whoever ends up holding it. Asked by the account itself, or by another
        account of the same person holding a role incompatible with it, that
        is one person granting themselves a role they could not be given.

        Args:
            account: The account whose role is handed out.
            by: The account asking (a token's owner), or None for a principal
                that is no account (the master token, root).
            action: What was attempted, for the message.

        Raises:
            AccountError: When it would be a grant to oneself.
        """
        if by is None:
            return
        if by.id == account.id:
            raise AccountError(
                f"You cannot {action} your own account",
                details="Another security officer can, or root with 'noust user'.",
            )
        if (
            by.person_ref
            and by.person_ref == account.person_ref
            and incompatible_roles(by.role, account.role)
            and not self._exception_in_force(by.person_ref)
        ):
            raise AccountError(
                f"You cannot {action} {account.username!r}: it belongs to you ({by.person_ref}) "
                f"and holds {account.role}, which is incompatible with your {by.role} role",
                details="Another security officer can, or root with 'noust user'.",
            )

    def _check_separation(
        self, person_ref: str | None, role: str, *, exclude_id: int | None
    ) -> None:
        """
        Refuse a role one person may not hold together with one they hold.

        Args:
            person_ref: The person the account belongs to; nothing is checked
                without one.
            role: The role being given.
            exclude_id: The account being changed, left out of the comparison.

        Raises:
            AccountError: When another account of the person holds an
                incompatible role and no exception is in force for them.
        """
        if not person_ref:
            return
        rows = self._rows(
            "SELECT username, role FROM accounts WHERE person_ref = ? AND status != ? AND id != ?",
            (person_ref, STATUS_DISABLED, -1 if exclude_id is None else exclude_id),
        )
        clashes = [row for row in rows if incompatible_roles(role, str(row["role"]))]
        if not clashes or self._exception_in_force(person_ref):
            return
        names = ", ".join(f"{row['username']} ({row['role']})" for row in clashes)
        raise AccountError(
            f"{person_ref} already holds a role incompatible with {role}: {names}",
            details=(
                "One person may not both run the infrastructure and govern its security, "
                "and an auditor holds no other role. If this installation has a single "
                "responsible person, record the exception with its reason and an end date: "
                f"noust user exception add {person_ref} --reason '...' --days 90."
            ),
        )

    def _exception_in_force(self, person_ref: str) -> bool:
        """
        Args:
            person_ref: The person.

        Returns:
            Whether a separation-of-duties exception covers them now.
        """
        return bool(
            self._rows(
                "SELECT 1 FROM account_sod_exceptions WHERE person_ref = ? AND revoked_at IS NULL "
                "AND expires_at > ? LIMIT 1",
                (person_ref, self.now()),
            )
        )

    def add_exception(
        self, person_ref: str, reason: str, *, days: int, created_by: str | None = None
    ) -> SodException:
        """
        Record a documented exception to the separation of duties.

        Args:
            person_ref: The person it covers.
            reason: Why; at least ten characters, it is what an auditor reads.
            days: How long it lasts, at most a year.
            created_by: Who recorded it.

        Returns:
            The exception.

        Raises:
            ValidationError: When the person, the reason or the duration is refused.
        """
        ref = clean_person_ref(person_ref)
        text = (reason or "").strip()
        if ref is None:
            raise ValidationError("A person reference is required", details="Such as an e-mail.")
        if len(text) < MIN_EXCEPTION_REASON:
            raise ValidationError(
                "The reason is too short",
                details=f"Explain it in at least {MIN_EXCEPTION_REASON} characters.",
            )
        if not 1 <= days <= MAX_EXCEPTION_DAYS:
            raise ValidationError(
                "An exception lasts between one day and a year",
                details="Record it again when it runs out, if it still applies.",
            )
        now = self.now()
        with self._write() as cursor:
            cursor.execute(
                "INSERT INTO account_sod_exceptions (person_ref, reason, expires_at, created_at, "
                "created_by) VALUES (?, ?, ?, ?, ?)",
                (ref, text[:1000], now + days * 86400, now, created_by),
            )
            exception_id = int(cursor.lastrowid or 0)
        return SodException(
            id=exception_id,
            person_ref=ref,
            reason=text[:1000],
            expires_at=now + days * 86400,
            created_at=now,
            created_by=created_by,
            revoked_at=None,
        )

    def list_exceptions(self) -> list[SodException]:
        """
        Returns:
            Every exception ever recorded, newest first.
        """
        rows = self._rows("SELECT * FROM account_sod_exceptions ORDER BY created_at DESC, id DESC")
        return [
            SodException(
                id=int(row["id"]),
                person_ref=str(row["person_ref"]),
                reason=str(row["reason"]),
                expires_at=float(row["expires_at"]),
                created_at=float(row["created_at"]),
                created_by=row["created_by"],
                revoked_at=row["revoked_at"],
            )
            for row in rows
        ]

    def revoke_exception(self, exception_id: int) -> bool:
        """
        Withdraw an exception before it runs out.

        Args:
            exception_id: Its id.

        Returns:
            True when one was withdrawn.
        """
        with self._write() as cursor:
            cursor.execute(
                "UPDATE account_sod_exceptions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (self.now(), int(exception_id)),
            )
            return cursor.rowcount > 0

    def separation_conflicts(self) -> list[dict[str, Any]]:
        """
        List the people who hold incompatible roles right now.

        For reporting (``noust user list``, the ENS check): an exception
        recorded for the person is named rather than hiding the conflict.

        Returns:
            One entry per person, with their accounts and whether an
            exception covers them.
        """
        rows = self._rows(
            "SELECT person_ref, username, role FROM accounts WHERE person_ref IS NOT NULL "
            "AND status != ? ORDER BY person_ref, username",
            (STATUS_DISABLED,),
        )
        by_person: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            by_person.setdefault(str(row["person_ref"]), []).append(
                (str(row["username"]), str(row["role"]))
            )
        conflicts = []
        for person, held in by_person.items():
            roles = [role for _name, role in held]
            if any(incompatible_roles(a, b) for i, a in enumerate(roles) for b in roles[i + 1 :]):
                conflicts.append(
                    {
                        "person_ref": person,
                        "accounts": [{"username": name, "role": role} for name, role in held],
                        "exception": self._exception_in_force(person),
                    }
                )
        return conflicts


def seal_totp_secrets_at_start(store: NoustStore | None = None) -> int:
    """
    Run the one-time migration of encryption at rest when the console starts.

    Secrets an earlier 3.1 build wrote in clear are sealed here, not in the
    schema migration: sealing needs the key file, and a schema step must
    only change the schema. A failure is logged and the console starts
    anyway; the secrets are then sealed as each one is used.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        How many secrets were sealed; 0 when it could not run.
    """
    try:
        sealed = AccountManager(store).seal_stored_totp_secrets()
    except (AccountError, OSError, sqlite3.Error) as exc:
        logger.error("Could not encrypt the stored TOTP secrets: %s", exc)
        return 0
    if sealed:
        logger.info("Encrypted %d TOTP secret(s) stored in clear", sealed)
    return sealed
