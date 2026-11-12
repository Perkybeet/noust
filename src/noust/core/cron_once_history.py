# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a one-time task leaves when it has run and retired itself (item 164).

A one-time task is a timer and a service that remove themselves once the command has ended. Every
other task keeps its result in systemd and its output in the journal, which are reached through
the units; a retired task has no units, so this is the one place its result and its output are,
for :data:`KEEP_DAYS` days and no longer. It is what the console lists under "ran once" and what a
person schedules again from.

The one reader and writer of ``cron_once_history`` (schema v18). The guards sit here and not in
the callers (rule 4): what is stored is masked of credentials by shape, however it arrived, and the
output is cut to its last lines.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from noust.core.secret_detection import mask_secret_shapes
from noust.core.timeutil import utc_now_iso

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["KEEP_DAYS", "MAX_OUTPUT_LINES", "CronOnceHistory", "OnceRun"]

logger = logging.getLogger(__name__)

#: Days a retired task stays in the record.
KEEP_DAYS = 30

#: The most lines of output kept: the end of a task's output is where it says what went wrong.
MAX_OUTPUT_LINES = 200

#: The most characters of a command or of a line of output: a line of a megabyte is not output.
_MAX_TEXT = 4000


@dataclass(frozen=True)
class OnceRun:
    """
    A one-time task that ran and retired.

    Attributes:
        name: The task's name, which was the name of its two units.
        command: The command line as it was typed.
        app_domain: The application it belonged to; empty for none.
        run_as: The account it ran as.
        working_directory: The directory it ran in; empty when none was set.
        timeout: Seconds it was allowed, or None for no limit.
        service: The Compose service it ran in; empty for this server.
        run_at: The moment it was asked for, ISO 8601 with the server's offset.
        started_at: When it started (ISO 8601, UTC), or None when the journal could not say.
        finished_at: When it ended (ISO 8601, UTC).
        result: systemd's word for how it ended: ``success``, ``exit-code``, ``timeout``,
            ``signal``..., or ``unknown``.
        exit_code: The command's exit status, or None when it did not exit with one.
        late: It started more than two minutes after ``run_at``: the server was off at that
            moment and ran it when it came back.
        output: The last lines it printed, credentials masked.
        id: The row; 0 before it is recorded.
        retired_at: When its units were removed (ISO 8601, UTC); empty before it is recorded.
    """

    name: str
    command: str
    run_at: str
    result: str
    app_domain: str = ""
    run_as: str = ""
    working_directory: str = ""
    timeout: int | None = None
    service: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    late: bool = False
    output: str = ""
    id: int = 0
    retired_at: str = ""

    @property
    def ok(self) -> bool:
        """Whether it ended without an error."""
        return self.result == "success"


def _bounded(value: str) -> str:
    """
    Mask what looks like a credential and bound the length of a line.

    Args:
        value: Text from a command line or a task's output.

    Returns:
        The text with each credential replaced and no line past :data:`_MAX_TEXT`.
    """
    masked = mask_secret_shapes(value)
    return "\n".join(line[:_MAX_TEXT] for line in masked.split("\n"))


def _tail(output: str) -> str:
    """
    Keep the last lines of an output.

    Args:
        output: What a task printed.

    Returns:
        At most :data:`MAX_OUTPUT_LINES` lines, with the final newline it had.
    """
    lines = output.splitlines()
    kept = "\n".join(lines[-MAX_OUTPUT_LINES:])
    return kept + "\n" if output.endswith("\n") and kept else kept


class CronOnceHistory:
    """The one-time tasks that ran, in the store's own connection."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    def record(self, run: OnceRun) -> int:
        """
        Keep a task that ran, once.

        A task is its name and the moment it was asked for: the hook that settles it and the
        monitor's sweep may both arrive, and the second finds the first's row.

        Args:
            run: What it was and how it ended. Its ``output`` and ``command`` are masked and cut
                here, whatever the caller did.

        Returns:
            The row's id.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM cron_once_history WHERE retired_at < ?",
                (self._horizon(),),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO cron_once_history "
                "(name, command, app_domain, run_as, working_directory, timeout, service, "
                "run_at, started_at, finished_at, result, exit_code, late, output, retired_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.name,
                    _bounded(run.command),
                    run.app_domain,
                    run.run_as,
                    run.working_directory,
                    run.timeout,
                    run.service,
                    run.run_at,
                    run.started_at,
                    run.finished_at,
                    run.result,
                    run.exit_code,
                    1 if run.late else 0,
                    _bounded(_tail(run.output)),
                    utc_now_iso("seconds"),
                ),
            )
            cursor.execute(
                "SELECT id FROM cron_once_history WHERE name = ? AND run_at = ?",
                (run.name, run.run_at),
            )
            row = cursor.fetchone()
        return int(row["id"])

    def recent(self) -> list[OnceRun]:
        """
        List the tasks that ran in the last :data:`KEEP_DAYS` days, forgetting the older ones.

        Returns:
            The runs, newest first.
        """
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "DELETE FROM cron_once_history WHERE retired_at < ?", (self._horizon(),)
                )
                cursor.execute("SELECT * FROM cron_once_history ORDER BY run_at DESC, id DESC")
                rows = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            # A store a development build opened before v18 had this table: nothing to show.
            logger.warning("Could not read the one-time tasks that ran: %s", exc)
            return []
        return [
            OnceRun(
                id=row["id"],
                name=row["name"],
                command=row["command"],
                app_domain=row["app_domain"],
                run_as=row["run_as"],
                working_directory=row["working_directory"],
                timeout=row["timeout"],
                service=row["service"],
                run_at=row["run_at"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                result=row["result"],
                exit_code=row["exit_code"],
                late=bool(row["late"]),
                output=row["output"],
                retired_at=row["retired_at"],
            )
            for row in rows
        ]

    @staticmethod
    def _horizon() -> str:
        """
        Returns:
            The moment before which a retired task is forgotten.
        """
        return (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(
            timespec="seconds"
        )
