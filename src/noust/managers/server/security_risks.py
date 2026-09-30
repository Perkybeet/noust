# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Risks an operator accepted instead of fixing: with a reason, a name and an end.

Some findings are deliberate: a staging box that keeps SSH passwords for a
client, a database port opened to one office. The ENS asks for such an
exception to be documented, not hidden, so accepting a risk records who, why
and until when, and a check shows "accepted" - not "passed" - until then.
Every acceptance ends: at most :data:`MAX_ACCEPT_DAYS` from now, and on
expiry the finding is back. Nothing is deleted; revoking or superseding an
acceptance stamps it, so the table is its own history.

Rows live in the ``server_accepted_risks`` table of schema v12.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core import audit as audit_trail
from noust.core.exceptions import ValidationError
from noust.managers.server.security_catalog import CATALOG

#: The longest a risk may be accepted for.
MAX_ACCEPT_DAYS = 365

#: Shortest and longest reason.
REASON_LENGTH = (10, 500)


def _utc(clock: Callable[[], datetime]) -> datetime:
    return clock().astimezone(timezone.utc)


@dataclass(frozen=True)
class AcceptedRisk:
    """
    One acceptance of one check's finding.

    Attributes:
        id: Row id.
        check_id: The check.
        reason: Why the finding is acceptable.
        accepted_by: Who accepted it.
        accepted_at: When, ISO 8601 with offset.
        expires_at: Until when, ISO 8601 with offset.
        revoked_at: When it was withdrawn or superseded, if it was.
        revoked_by: By whom.
    """

    id: int
    check_id: str
    reason: str
    accepted_by: str
    accepted_at: str
    expires_at: str
    revoked_at: str | None = None
    revoked_by: str | None = None

    def active(self, now: datetime) -> bool:
        """
        Report whether the acceptance still holds.

        Args:
            now: The current time, aware.

        Returns:
            True until it is revoked or expires.
        """
        return self.revoked_at is None and datetime.fromisoformat(self.expires_at) > now

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the acceptance for the API and ``--json``.

        Returns:
            Every field.
        """
        return asdict(self)


class AcceptedRisks:
    """
    Read and write accepted risks.

    Args:
        store: The store; the process-wide one by default.
        clock: The current time, aware.
    """

    def __init__(
        self,
        store: Any = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self.clock = clock

    @property
    def store(self) -> Any:
        """The store."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    @staticmethod
    def _row(row: sqlite3.Row) -> AcceptedRisk:
        return AcceptedRisk(
            id=int(row["id"]),
            check_id=str(row["check_id"]),
            reason=str(row["reason"]),
            accepted_by=str(row["accepted_by"]),
            accepted_at=str(row["accepted_at"]),
            expires_at=str(row["expires_at"]),
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"],
        )

    def history(self, check_id: str | None = None) -> list[AcceptedRisk]:
        """
        Every acceptance, newest first.

        Args:
            check_id: Only this check's, when given.

        Returns:
            The acceptances, revoked and expired included.
        """
        connection = self.store._get_connection()
        if check_id is None:
            rows = connection.execute(
                "SELECT * FROM server_accepted_risks ORDER BY id DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM server_accepted_risks WHERE check_id = ? ORDER BY id DESC",
                (check_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def active(self) -> dict[str, AcceptedRisk]:
        """
        The acceptances that hold now.

        Returns:
            Check id to its current acceptance.
        """
        now = _utc(self.clock)
        found: dict[str, AcceptedRisk] = {}
        for risk in self.history():
            if risk.check_id not in found and risk.active(now):
                found[risk.check_id] = risk
        return found

    def accept(self, check_id: str, *, reason: str, by: str, expires_at: datetime) -> AcceptedRisk:
        """
        Accept a check's finding until a date.

        An earlier acceptance of the same check that still holds is
        superseded: stamped as revoked by the same person, now.

        Args:
            check_id: The check.
            reason: Why, 10 to 500 characters.
            by: Who.
            expires_at: Until when: in the future, at most
                :data:`MAX_ACCEPT_DAYS` away. A naive value is read as UTC.

        Returns:
            The acceptance.

        Raises:
            ValidationError: The check, the reason or the date is not acceptable.
        """
        if check_id not in CATALOG:
            raise ValidationError(f"There is no check {check_id!r}", field="check_id")
        text = " ".join((reason or "").split())
        low, high = REASON_LENGTH
        if not low <= len(text) <= high:
            raise ValidationError(
                f"Say why the risk is acceptable, in {low} to {high} characters",
                field="reason",
            )
        now = _utc(self.clock)
        until = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        until = until.astimezone(timezone.utc)
        if until <= now:
            raise ValidationError("The acceptance must end in the future", field="expires_at")
        if until > now + timedelta(days=MAX_ACCEPT_DAYS):
            raise ValidationError(
                f"A risk is accepted for {MAX_ACCEPT_DAYS} days at most; accept it again then",
                field="expires_at",
            )
        stamp = now.isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE server_accepted_risks SET revoked_at = ?, revoked_by = ? "
                "WHERE check_id = ? AND revoked_at IS NULL",
                (stamp, by, check_id),
            )
            cursor.execute(
                "INSERT INTO server_accepted_risks "
                "(check_id, reason, accepted_by, accepted_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (check_id, text, by, stamp, until.isoformat(timespec="seconds")),
            )
            row_id = cursor.lastrowid
            row = cursor.execute(
                "SELECT * FROM server_accepted_risks WHERE id = ?", (row_id,)
            ).fetchone()
        accepted = self._row(row)
        audit_trail.record(
            "security.risk.accept",
            target=f"check:{check_id}",
            details={"reason": text, "expires_at": accepted.expires_at, "by": by},
        )
        return accepted

    def revoke(self, check_id: str, *, by: str) -> AcceptedRisk | None:
        """
        Withdraw the acceptance of a check, so its finding shows again.

        Args:
            check_id: The check.
            by: Who.

        Returns:
            The acceptance withdrawn, or None when none held.
        """
        current = self.active().get(check_id)
        if current is None:
            return None
        stamp = _utc(self.clock).isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE server_accepted_risks SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                (stamp, by, current.id),
            )
        audit_trail.record("security.risk.revoke", target=f"check:{check_id}", details={"by": by})
        return AcceptedRisk(**{**asdict(current), "revoked_at": stamp, "revoked_by": by})
