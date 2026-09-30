# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The reboots and shutdowns an operator scheduled, as rows of the store.

Not a job: a job lives in the process that a reboot kills. The row is what the
console reads after the machine is back, to say "the reboot you asked for
happened", and what it reads before, to say "a reboot is scheduled for 21:40 by
yago, cancel it here".

The table is ``server_power_actions`` of schema v12. The store owns its
connection and its dry-run rule (a rehearsal rolls its transactions back), so
this goes through the store's own transaction rather than opening the file: a
second way to write the store is how a ``--dry-run`` ends up leaving a row behind.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store

#: The two things that can be scheduled.
ACTIONS = ("reboot", "poweroff")


@dataclass(frozen=True)
class ScheduledPower:
    """
    A reboot or shutdown that was asked for.

    Attributes:
        id: Row identifier.
        action: ``reboot`` or ``poweroff``.
        scheduled_for: When it is due, ISO 8601 UTC.
        requested_at: When it was asked for, ISO 8601 UTC.
        requested_by: Who asked, as the audit log names them.
        message: The wall message sent to logged-in users.
        boot_id: The boot the request was made in.
        status: ``scheduled``, ``cancelled``, ``completed`` or ``lost``.
        finished_at: When the row stopped being scheduled.
    """

    id: int
    action: str
    scheduled_for: str
    requested_at: str
    requested_by: str | None
    message: str | None
    boot_id: str
    status: str
    finished_at: str | None = None

    def to_dict(self) -> dict:
        """
        Render the row as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


def _now() -> str:
    """
    Read the clock as the rows write it.

    Returns:
        ISO 8601 UTC.
    """
    return datetime.now(timezone.utc).isoformat()


class PowerRecords:
    """Reads and writes the schedule."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when omitted.
        """
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store in force right now."""
        return self._store or get_store()

    def schedule(
        self,
        action: str,
        scheduled_for: str,
        *,
        requested_by: str | None,
        message: str | None,
        boot_id: str,
    ) -> ScheduledPower:
        """
        Record a new schedule, replacing the one that was there.

        ``shutdown`` keeps a single pending action, so scheduling another one
        replaces the first, and so does the record.

        Args:
            action: ``reboot`` or ``poweroff``.
            scheduled_for: When it is due, ISO 8601 UTC.
            requested_by: Who asked.
            message: The wall message.
            boot_id: The current boot.

        Returns:
            The new row.
        """
        now = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE server_power_actions SET status = 'cancelled', finished_at = ? "
                "WHERE status = 'scheduled'",
                (now,),
            )
            cursor.execute(
                "INSERT INTO server_power_actions "
                "(action, scheduled_for, requested_at, requested_by, message, boot_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (action, scheduled_for, now, requested_by, message, boot_id),
            )
            row = cursor.execute(
                "SELECT * FROM server_power_actions WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return ScheduledPower(**dict(row))

    def active(self) -> ScheduledPower | None:
        """
        Read the pending schedule.

        Returns:
            The row that is still scheduled, or None.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM server_power_actions WHERE status = 'scheduled' "
                "ORDER BY id DESC LIMIT 1"
            )
            .fetchone()
        )
        return ScheduledPower(**dict(row)) if row else None

    def finish(self, action_id: int, status: str) -> bool:
        """
        Close a schedule.

        Args:
            action_id: The row.
            status: ``cancelled``, ``completed`` or ``lost``.

        Returns:
            True when a scheduled row was closed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE server_power_actions SET status = ?, finished_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (status, _now(), action_id),
            )
            return cursor.rowcount > 0

    def history(self, limit: int = 10) -> list[ScheduledPower]:
        """
        List the most recent schedules.

        Args:
            limit: How many.

        Returns:
            Rows, newest first.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM server_power_actions ORDER BY id DESC LIMIT ?", (limit,))
            .fetchall()
        )
        return [ScheduledPower(**dict(row)) for row in rows]
