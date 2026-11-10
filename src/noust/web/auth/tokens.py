# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The token manager: sign-in, sessions, API tokens, second factor, tickets and elevation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core import paths, totp
from noust.core.accounts import AccountManager, AuthPolicy
from noust.core.accounts.model import Account
from noust.core.console_exposure_store import console_published
from noust.core.exceptions import SecurityError
from noust.core.fs import SECRET_MODE, get_fs
from noust.core.store import StoreError
from noust.web.auth.clock import _now, utcnow
from noust.web.auth.config import SecurityConfig
from noust.web.auth.connection import ip_matches
from noust.web.auth.constants import (
    API_TOKEN_LAST_USED_THROTTLE,
    API_TOKEN_PREFIX,
    API_TOKEN_PREFIXES,
    API_TOKEN_SCOPES,
    API_TOKEN_SID_PREFIX,
    BACKUP_CODE_COUNT,
    ELEVATION_SLIDE_THRESHOLD,
    FLEET_SCOPE,
    LEGACY_API_TOKEN_PREFIX,
    LOGIN_CHALLENGE_ATTEMPTS,
    LOGIN_CHALLENGE_TTL,
    MASTER_SID,
    MAX_ROTATION_HOPS,
    SECRET_KEY_LENGTH,
    SESSION_ACTIVITY_THROTTLE,
    SESSION_ISSUER,
    SESSION_KIND_ACCOUNT,
    SESSION_KIND_MASTER,
    SESSION_RENEW_RATIO,
    SESSION_SUBJECT,
    STATE_DIR_ENV,
    TOKEN_LENGTH,
    WS_TICKET_TTL,
)
from noust.web.auth.files import _file_stamp, _generation, write_private_file
from noust.web.auth.payload import CredentialRefused, master_payload
from noust.web.auth.records import (
    ENS_TOKEN_EXCLUDED,
    LEGACY_TOKEN_CEILING,
    LEGACY_TOKEN_WINDOW_DAYS,
    _api_enabled,
    _json_list,
    _validated_networks,
)
from noust.web.auth.sessions import SessionStore
from noust.web.permissions import ALL_PERMISSIONS, Permission
from noust.web.permissions.roles import (
    GRANT_BREAK_GLASS,
    GRANT_COMPAT,
    GRANT_PERMISSIONS,
    GRANT_RECOVERY,
    LEGACY_SCOPES,
    TOKEN_SCOPES,
    instance_role_of,
    legacy_scope,
    permissions_for_scope,
    permissions_of_account,
)
from noust.web.permissions.team_roles import (
    BOUND_KINDS,
    TEAM_PAYLOAD_KEYS,
    bound_fields,
    bound_reach,
    team_fields,
)

logger = logging.getLogger("noust.web.auth")


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
            ``recovery`` where the ``master_token`` control is on (account
            recovery only),
            ``break_glass`` once accounts exist, ``compat`` before (the whole
            console, exactly as in 3.0).
        """
        if self.policy().requires("master_token"):
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
        # What the role holds outside any team, and what its team roles add:
        # the payload carries both so a route is held to the ceiling first and
        # to the resource's own team after (noust.web.permissions.team_roles).
        teams = team_fields(
            account.id,
            instance_role_of(account),
            permissions_of_account(account, self.accounts.store),
            self.accounts.store,
        )
        permissions = teams["permissions"]
        policy = self.policy()
        notice = policy.notice_version
        # Held at the enrolment only where somebody besides this machine's operator can reach
        # the sign-in form, or where a framework asks for it; elsewhere it is a step the person
        # takes now or later. `noust console expose` refuses to publish with an account lacking
        # one in any case (managers.console_exposure.second_factor_blockers).
        mfa_required = policy.requires("second_factor") or console_published()
        return {
            "account_id": account.id,
            "username": account.username,
            "role": account.role,
            "roles": list(account.roles),
            **teams,
            "scope": legacy_scope(permissions),
            "mfa_pending": not account.has_mfa and mfa_required,
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
        # A master token issued before 3.0 starts with the old product's name; it is
        # verified by its hash, so it keeps working until it is replaced.
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

        Sudo mode takes a code without the token, so a code for ``elevate``
        also has to be newer than the last one that signed in
        (:data:`noust.core.totp.STEP_FLOORS`): the code typed at sign-in
        cannot be replayed to elevate a session.

        Args:
            code: What the client typed.
            purpose: What the code is being spent on - ``login``, ``elevate``
                or ``disable``. Remembered per purpose, and ``elevate``
                follows ``login``: the operator who signs in and then
                confirms a destructive action waits for the next code.

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
                stored = state.get("last_steps")
                spent = (
                    {str(name): value for name, value in stored.items() if isinstance(value, int)}
                    if isinstance(stored, dict)
                    else {}
                )
                last = totp.newest_spent_step(spent, purpose)
                if last is not None and step <= last:
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
        bound: tuple[str, int] | None = None,
        issuer_permissions: frozenset[str] | None = None,
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
                token cannot open by itself. Refused where the compliance control
                ``restricted_tokens`` is on, and issued only by whoever governs
                accounts (see ``issuer_permissions``).
            created_by: Who issued it, for the record.
            bound: ``(kind, id)`` of the team, project or environment the token
                is bound to (I-34): it sees that and nothing else, and holds at
                most what its owner holds there, never what reaches the machine.
                The owner must reach it.
            issuer_permissions: What the person or credential asking for the
                token holds, when it is not root (the console passes the
                session's; the command line is root and leaves it out). A token
                that acts in sudo mode is a standing exemption from a fresh
                proof, so only whoever governs accounts may hand one out, and
                nobody hands one to themselves from a lesser role.

        Returns:
            The record, including the one and only clear copy of the token
            under ``"token"``.

        Raises:
            SecurityError: When the name is empty or taken, the scope is not
                a scope, is ``fleet`` (see :meth:`create_fleet_token`), the
                expiry is not positive or too long, a permission or network
                is not valid, the owner does not hold a permission asked, or a
                token is bound without an owner or to a scope its owner does
                not reach.
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
        if owner is None and policy.requires("restricted_tokens"):
            raise SecurityError(
                "On this server an API token belongs to an account",
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
                    f"API tokens last at most {policy.token_max_days} days on this server",
                    details=(
                        f"Ask for {int(longest)} hours or fewer, and issue a new one then. "
                        f"A compliance framework asks for it: noust compliance status."
                    ),
                )
        if allow_elevated and policy.requires("restricted_tokens"):
            raise SecurityError(
                "API tokens cannot act in sudo mode on this server",
                details="Do what needs confirming from the console, as a person.",
            )
        if (
            allow_elevated
            and issuer_permissions is not None
            and Permission.ACCOUNTS_MANAGE not in issuer_permissions
        ):
            raise SecurityError(
                "Only whoever governs accounts can issue an API token that acts in sudo mode",
                details=(
                    "Ask an administrator to issue it, or issue the token without allow_elevated "
                    "and do what needs confirming from the console, as a person."
                ),
            )
        stored_permissions: list[str] | None = None
        if bound is not None and owner is None:
            raise SecurityError(
                "A token bound to a team, project or environment belongs to an account",
                details="Issue it signed in as that account, or with '--owner USERNAME'.",
            )
        if owner is not None:
            # A token never holds more than its owner does anywhere: the role's
            # permissions and what the owner's team roles add - and, bound to a
            # scope, only what the owner holds there.
            owned = team_fields(
                owner.id,
                instance_role_of(owner),
                permissions_of_account(owner, self.accounts.store),
                self.accounts.store,
            )
            if bound is not None:
                kind, bound_id = bound
                if kind not in BOUND_KINDS or not bound_reach(
                    owned, kind, bound_id, self.accounts.store
                ):
                    raise SecurityError(
                        f"{owner.username!r} does not reach that {kind}",
                        details=(
                            "A token bound to a scope can only be issued by someone who has a "
                            "role there: give the account one first ('noust access grant')."
                        ),
                    )
                owned = {**owned, **bound_fields(owned, kind, bound_id, self.accounts.store)}
            held = owned["permissions"]
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
            bound_kind=bound[0] if bound is not None else None,
            bound_id=bound[1] if bound is not None else None,
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
        bound_kind: str | None = None,
        bound_id: int | None = None,
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
            bound_kind: The kind of scope it is bound to, already checked.
            bound_id: That scope's row.

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
                bound_kind=bound_kind,
                bound_id=bound_id,
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
            "bound_kind": bound_kind,
            "bound_id": bound_id,
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

    def set_api_token_networks(
        self, token_id: int, entries: list[str] | None
    ) -> dict[str, Any] | None:
        """
        Restrict a live API token to networks, or lift the restriction.

        Args:
            token_id: The record's id, as listed.
            entries: Addresses or CIDRs; None or empty for any.

        Returns:
            The record as it is now, or None when there is no live token with that id.

        Raises:
            SecurityError: When an entry is not an address or a network.
        """
        networks = _validated_networks(entries)
        if not self.sessions.set_api_token_networks(token_id, networks):
            return None
        return self.sessions.get_api_token_by_id(token_id)

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

        return self._api_token_payload(
            record, client_ip, legacy_prefix=token.startswith(LEGACY_API_TOKEN_PREFIX)
        )

    def legacy_prefix_tokens_in_use(
        self, days: float = LEGACY_TOKEN_WINDOW_DAYS
    ) -> list[dict[str, Any]]:
        """
        Name the tokens that were presented with the prefix of before 3.0 lately.

        Args:
            days: How far back a use counts.

        Returns:
            The live tokens, most recently used first.
        """
        return self.sessions.legacy_prefix_tokens(time.time() - days * 86400)

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
        self, record: Mapping[str, Any], client_ip: str | None, *, legacy_prefix: bool = False
    ) -> dict[str, Any] | None:
        """
        Turn an unrevoked API token row into the payload it authenticates.

        Shared by a Bearer token and a WebSocket ticket issued to one, so the
        two cannot disagree about expiry or about the scope a token carries.

        Args:
            record: The token row, already known to be unrevoked.
            client_ip: Address presenting the credential.
            legacy_prefix: The credential was presented with the prefix of before 3.0.

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
        recorded = record.get("legacy_prefix_used_at")
        if (
            last_used is None
            or now - float(last_used) >= API_TOKEN_LAST_USED_THROTTLE
            or (legacy_prefix and recorded is None)
        ):
            self.sessions.touch_api_token(
                int(record["id"]), now, client_ip, legacy_prefix=legacy_prefix
            )
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
        if not _api_enabled():
            raise CredentialRefused(
                403,
                "api_disabled",
                "The API is turned off on this server.",
                "Use the console, or turn it on with: noust config set web.api_enabled true",
                action="auth.token.denied",
            )
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
        # A token nobody owns acts as the instance and sees every team; one an
        # account owns carries the teams of that account.
        team_part: dict[str, Any] = {}
        bound_kind = record.get("bound_kind")
        if owner_id is None:
            if bound_kind:
                # A token bound to a scope has an owner or it is nothing: refuse it rather
                # than let it act as the instance.
                raise CredentialRefused(
                    401,
                    "token_owner_inactive",
                    f"The API token {record['name']!r} is bound to a scope but has no owner.",
                    "Issue a new token from an account.",
                    action="auth.token.denied",
                )
            if policy.requires("restricted_tokens"):
                raise CredentialRefused(
                    401,
                    "legacy_token_refused",
                    f"The API token {record['name']!r} belongs to no account, which this "
                    "server's compliance controls do not accept.",
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
            teams = team_fields(
                owner.id,
                instance_role_of(owner),
                permissions_of_account(owner, self.accounts.store),
                self.accounts.store,
            )
            if bound_kind and record.get("bound_id") is not None:
                teams = {
                    **teams,
                    **bound_fields(
                        teams, str(bound_kind), int(record["bound_id"]), self.accounts.store
                    ),
                }
            permissions = requested & teams["permissions"]
            team_part = {key: teams[key] for key in TEAM_PAYLOAD_KEYS}
        restricted_tokens = policy.requires("restricted_tokens")
        if restricted_tokens:
            permissions = permissions - ENS_TOKEN_EXCLUDED
        allow_elevated = record.get("allow_elevated")
        notice = policy.notice_version
        return {
            **team_part,
            "permissions": frozenset(permissions),
            "scope": legacy_scope(permissions),
            "role": role,
            "roles": list(owner.roles) if owner is not None else None,
            "owner_account_id": owner_id,
            "elevation_exempt": (allow_elevated is None or bool(allow_elevated))
            and not restricted_tokens,
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
                ``password``, ``passkey`` or ``oidc``.

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
        # When the login this session descends from happened, which a renewal keeps: what a recent
        # proof of identity is measured from (noust.web.api.identity_proof).
        payload["signed_in_at"] = float(record.get("created_at") or record["issued_at"])
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

    def elevate(self, sid: str) -> float:
        """
        Confirm a session for sudo mode: the destructive actions D5 names.

        Opens the window for the policy's idle time
        (``auth.sudo.idle_minutes``) and starts the clock its ceiling is
        counted from (``auth.sudo.max_minutes``); :meth:`extend_elevation`
        keeps it open while elevated actions are performed.

        A request that still carries the cookie a rotation retired confirms
        the successor too, and its deadline is worked out from the successor:
        the retired row ends within :data:`SESSION_ROTATION_GRACE` seconds and
        the browser is already moving to the other one, so sudo mode written
        only to the retired row would vanish with it.

        Args:
            sid: Session identifier being elevated.

        Returns:
            The elevation deadline, as a UNIX timestamp.
        """
        now = _now()
        lineage = self._lineage(sid)
        elevated_until = self._elevation_deadline(
            lineage[-1] if lineage else None, now=now, confirmed_at=now
        )
        for row in lineage or [{"sid": sid}]:
            self.sessions.set_elevated(str(row["sid"]), elevated_until, now)
        return elevated_until

    def _lineage(self, sid: str) -> list[dict[str, Any]]:
        """
        Follow a session along its rotations to the one that is live now.

        Args:
            sid: The session a request presented.

        Returns:
            Its row, then the row of each successor that is still live, the
            last one being the session the browser is moving to; just its own
            row when it was not rotated (or its successor is gone), and empty
            when it is not a live session at all.
        """
        chain: list[dict[str, Any]] = []
        record = self.sessions.get(sid)
        while record is not None and len(chain) < MAX_ROTATION_HOPS:
            chain.append(record)
            successor = record.get("rotated_to")
            if not successor:
                break
            record = self.sessions.get(str(successor))
        return chain

    def extend_elevation(self, payload: dict[str, Any]) -> float | None:
        """
        Keep sudo mode open for a session that just performed an elevated action.

        Called where an elevated action is allowed to proceed
        (:func:`noust.web.api.deps.ensure_elevated`), never for a request that
        merely arrived: reading the console does not keep sudo mode alive. The
        new deadline is the idle window from now, never past the ceiling
        counted from the confirmation, and never past the session's own end.
        It is written only when that gains more than
        :data:`ELEVATION_SLIDE_THRESHOLD` seconds.

        Args:
            payload: The verified payload of the session that acted. Its
                ``elevated_until`` is updated in place when the window moved,
                so the rest of the request sees the same deadline the store
                holds.

        Returns:
            The new deadline, or None when nothing changed: the session is
            not inside sudo mode, was confirmed before sessions recorded when
            (it ends where it was meant to), or the gain was too small to be
            worth a write.
        """
        sid = payload.get("sid")
        current = payload.get("elevated_until")
        if not sid or current is None:
            return None
        now = _now()
        if float(current) <= now:
            return None
        # Cheap refusal first, from what the request already carries: most
        # elevated requests come right after the last one moved the window.
        if now + self._elevation_idle_seconds() - float(current) <= ELEVATION_SLIDE_THRESHOLD:
            return None
        # The live session's row, not the retired one a request in flight still
        # names: its end is cut to the rotation grace, and it is the successor
        # that has to keep the window the operator is working in.
        lineage = self._lineage(str(sid))
        record = lineage[-1] if lineage else None
        if record is None or record.get("elevated_at") is None:
            return None
        deadline = self._elevation_deadline(
            record, now=now, confirmed_at=float(record["elevated_at"])
        )
        if deadline - float(current) <= ELEVATION_SLIDE_THRESHOLD:
            return None
        for row in lineage:
            self.sessions.slide_elevated(str(row["sid"]), deadline)
        payload["elevated_until"] = deadline
        return deadline

    def _elevation_idle_seconds(self) -> float:
        """
        Returns:
            How long sudo mode stays open after the last elevated action.
        """
        return float(self.policy().sudo_idle_minutes) * 60.0

    def _elevation_deadline(
        self, record: Mapping[str, Any] | None, *, now: float, confirmed_at: float
    ) -> float:
        """
        Work out where sudo mode ends, as of one elevated action.

        Args:
            record: The session row, or None when it is not in the store (an
                API token's pseudo-session), which leaves only the policy.
            now: The moment of the action.
            confirmed_at: When sudo mode was confirmed.

        Returns:
            The earliest of: the idle window from ``now``, the policy's
            ceiling from ``confirmed_at``, the session's expiry and the end
            of the login it descends from.
        """
        policy = self.policy()
        deadline = min(
            now + float(policy.sudo_idle_minutes) * 60.0,
            confirmed_at + float(policy.sudo_max_minutes) * 60.0,
        )
        if record is not None:
            created = float(record.get("created_at") or record["issued_at"])
            deadline = min(
                deadline, float(record["expires_at"]), created + self._absolute_seconds()
            )
        return deadline

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
            if self.policy().requires("master_token"):
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

    def issue_login_challenge(
        self, account_id: int, client_ip: str, via: str = "password"
    ) -> tuple[str, int]:
        """
        Open the second step of an account's sign-in, once its first step passed.

        The value is random and only its hash is stored, so it cannot be
        forged or read back from the database; it is bound to the account and
        to the address the password came from, lasts
        :data:`LOGIN_CHALLENGE_TTL` seconds, and is spent by the sign-in it
        completes. It stands for the password in the second step, so the
        password never travels twice.

        Args:
            account_id: The account whose first step was right.
            client_ip: The address it came from.
            via: What the first step was: ``password``, or ``oidc`` for a
                person their identity provider vouched for.

        Returns:
            The challenge and its lifetime in seconds.
        """
        challenge = secrets.token_urlsafe(32)
        self.sessions.store_login_challenge(
            hashlib.sha256(challenge.encode()).hexdigest(),
            account_id,
            client_ip,
            time.time() + LOGIN_CHALLENGE_TTL,
            via,
        )
        return challenge, LOGIN_CHALLENGE_TTL

    def login_challenge_via(self, challenge: str) -> str:
        """
        Say what the first step of a sign-in was, without spending it.

        Args:
            challenge: The value presented.

        Returns:
            ``password`` or ``oidc``; ``password`` for one that is not open.
        """
        record = (
            self.sessions.get_login_challenge(hashlib.sha256(challenge.encode()).hexdigest())
            if challenge
            else None
        )
        return str(record["via"]) if record is not None and record.get("via") else "password"

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
