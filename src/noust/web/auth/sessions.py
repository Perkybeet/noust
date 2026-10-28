# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQLite store of sessions, API tokens, tickets and login challenges.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from noust.core.exceptions import SecurityError
from noust.core.fs import is_rehearsal
from noust.web.auth.clock import _now
from noust.web.auth.constants import (
    FILE_MODE,
    FLEET_SCOPE,
    SESSION_KIND_MASTER,
    SESSION_ROTATION_GRACE,
    STATE_DIR_ENV,
)
from noust.web.auth.files import ensure_state_dir
from noust.web.auth.records import (
    API_TOKEN_BINDING_COLUMNS,
    API_TOKEN_COLUMNS_3_1,
    API_TOKEN_COLUMNS_4_0,
    API_TOKENS_TABLE_SQL,
    _token_record,
)


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
            if "elevated_at" not in columns:
                # When sudo mode was confirmed, which the sliding window's
                # ceiling is counted from. NULL for a session elevated before
                # this column existed: its window is never extended, so it ends
                # at the deadline it was given.
                self._conn.execute("ALTER TABLE sessions ADD COLUMN elevated_at REAL")
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
                    failures INTEGER NOT NULL DEFAULT 0,
                    via TEXT NOT NULL DEFAULT 'password'
                )
                """
            )
            challenge_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(login_challenges)").fetchall()
            }
            if "via" not in challenge_columns:
                # What the first step was: a password, or a single sign-in
                # through an identity provider. Every step before 4.0 was a password.
                self._conn.execute(
                    "ALTER TABLE login_challenges ADD COLUMN via TEXT NOT NULL DEFAULT 'password'"
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
            for column, declaration in (
                *API_TOKEN_COLUMNS_3_1,
                *API_TOKEN_BINDING_COLUMNS,
                *API_TOKEN_COLUMNS_4_0,
            ):
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
            auth_method: How it signed in: ``password``, ``token``, ``passkey``, ``oidc``.
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

    def set_elevated(
        self, sid: str, elevated_until: float | None, elevated_at: float | None = None
    ) -> None:
        """
        Record or clear a session's sudo-mode confirmation.

        Args:
            sid: Session identifier.
            elevated_until: Timestamp the elevation expires at, or None to
                drop it, for example on logout.
            elevated_at: When it was confirmed, the point its ceiling is
                counted from. Cleared together with ``elevated_until``.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET elevated_until = ?, elevated_at = ? WHERE sid = ?",
                (elevated_until, elevated_at if elevated_until is not None else None, sid),
            )

    def slide_elevated(self, sid: str, elevated_until: float) -> None:
        """
        Push a confirmed session's sudo-mode deadline out, keeping when it was confirmed.

        Args:
            sid: Session identifier.
            elevated_until: The new deadline. Only a session that has been
                confirmed (has ``elevated_at``) is touched.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET elevated_until = ? WHERE sid = ? AND elevated_at IS NOT NULL",
                (elevated_until, sid),
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
                "elevated_until, elevated_at, family, kind, account_id, auth_method, "
                "last_activity) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_sid,
                    csrf_token,
                    row["client_ip"],
                    now,
                    row["created_at"],
                    expires_at,
                    # Renewal must not reset the sudo-mode window, nor move its
                    # ceiling: it carries what is left of both to the new
                    # session id.
                    row["elevated_until"],
                    row["elevated_at"],
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
        bound_kind: str | None = None,
        bound_id: int | None = None,
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
            bound_kind: ``team``, ``project`` or ``environment`` when the token
                is bound to one, else None.
            bound_id: That scope's row.

        Returns:
            The new record's id.

        Raises:
            sqlite3.IntegrityError: When the name is already taken.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO api_tokens (name, token_hash, scope, created_at, expires_at, "
                "owner_account_id, permissions, allowed_cidrs, allow_elevated, created_by, "
                "bound_kind, bound_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    bound_kind,
                    bound_id,
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
            The record as the list shows it (its JSON columns decoded), revoked
            or not, or None.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_tokens WHERE id = ?", (token_id,)
            ).fetchone()
        return None if row is None else _token_record(row)

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

    def touch_api_token(
        self,
        token_id: int,
        used_at: float,
        used_ip: str | None = None,
        *,
        legacy_prefix: bool = False,
    ) -> None:
        """
        Record when a token last authenticated a request, and from where.

        Args:
            token_id: The token record's id.
            used_at: The moment of use, as a UNIX timestamp.
            used_ip: The address it came from.
            legacy_prefix: The token was presented with the prefix of before 3.0.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE api_tokens SET last_used_at = ?, last_used_ip = COALESCE(?, last_used_ip), "
                "legacy_prefix_used_at = CASE WHEN ? THEN ? ELSE legacy_prefix_used_at END "
                "WHERE id = ?",
                (used_at, used_ip, int(legacy_prefix), used_at, token_id),
            )

    def legacy_prefix_tokens(self, since: float) -> list[dict[str, Any]]:
        """
        List the live tokens that signed in with the prefix of before 3.0 since a moment.

        Args:
            since: The earliest use that counts, as a UNIX timestamp.

        Returns:
            One row per token (``id``, ``name``, ``scope``, ``last_used_at``), most recent first.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, scope, last_used_at, legacy_prefix_used_at FROM api_tokens "
                "WHERE revoked_at IS NULL AND legacy_prefix_used_at IS NOT NULL "
                "AND legacy_prefix_used_at >= ? AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY legacy_prefix_used_at DESC, id DESC",
                (since, time.time()),
            ).fetchall()
        return [dict(row) for row in rows]

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
                "last_used_ip, bound_kind, bound_id FROM api_tokens "
                "WHERE (? IS NULL OR owner_account_id = ?) "
                "ORDER BY created_at DESC, id DESC",
                (owner_account_id, owner_account_id),
            ).fetchall()
        return [_token_record(row) for row in rows]

    def set_api_token_networks(self, token_id: int, networks: list[str] | None) -> bool:
        """
        Change the networks a live API token is accepted from.

        Args:
            token_id: The token record's id.
            networks: The networks in canonical form, or None for any.

        Returns:
            Whether a live token with that id was changed.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE api_tokens SET allowed_cidrs = ? WHERE id = ? AND revoked_at IS NULL",
                (None if networks is None else json.dumps(networks), token_id),
            )
            return cursor.rowcount > 0

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
        self,
        challenge_hash: str,
        account_id: int,
        client_ip: str,
        expires_at: float,
        via: str = "password",
    ) -> None:
        """
        Persist the second step of an account's sign-in.

        Args:
            challenge_hash: Hash of the challenge value.
            account_id: The account whose first step passed.
            client_ip: The address it passed from.
            expires_at: Expiry as a UNIX timestamp.
            via: What the first step was: ``password`` or ``oidc``.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO login_challenges "
                "(challenge_hash, account_id, client_ip, expires_at, via) VALUES (?, ?, ?, ?, ?)",
                (challenge_hash, int(account_id), client_ip, expires_at, via),
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
