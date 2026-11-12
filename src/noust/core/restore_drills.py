# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a restore drill found, and its record in the store.

A drill (:mod:`noust.managers.restore_drill`) rehearses restoring an application's newest backup and
keeps a dated report: what it checked, step by step, and how long it took. The report and the rows it
is kept as are here, below the manager that makes them, because the server's evidence of a restore
that was tried (``noust ens check``, ``noust ens report``) reads them too and must not import what
runs the drill (rule 3: one implementation of what a drill's record is).

What a drill proves, and what it does not: the archive is proven to read and unpack and the data to
load into a temporary database, not that a new server would start with it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from noust.core.store import NoustStore, get_store

#: Steps that are not a database: the backup chosen, the archive's check and the unpacking, and a
#: drill that could not run at all. Every other step loaded the dump of a database, or tried to.
_NOT_A_DATABASE = frozenset({"backup", "archive", "unpack", "drill"})


@dataclass(frozen=True)
class DrillStep:
    """
    One thing a drill checked.

    Attributes:
        name: What it was (``archive``, ``unpack``, ``postgresql/shop``...).
        ok: Whether it passed. A step that was not tried is ``ok`` and ``skipped``.
        detail: What was found, in a sentence, or what went wrong, verbatim.
        skipped: Whether it was not tried, and ``detail`` says why.
    """

    name: str
    ok: bool
    detail: str
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the step as plain data.

        Returns:
            Its attributes.
        """
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "skipped": self.skipped}


@dataclass
class DrillReport:
    """
    What a drill found, with its date.

    Attributes:
        domain: The application.
        backup_id: The backup rehearsed; None when there was none.
        trigger: ``manual`` or ``schedule``.
        started_at: When it began (UTC, ISO 8601).
        finished_at: When it ended.
        steps: Everything it checked, in order.
        id: The row it was kept as.
    """

    domain: str
    backup_id: str | None
    trigger: str
    started_at: str
    finished_at: str = ""
    steps: list[DrillStep] = field(default_factory=list)
    id: int | None = None

    @property
    def ok(self) -> bool:
        """Whether every step that was tried passed, and at least one was."""
        return bool(self.steps) and all(step.ok for step in self.steps)

    @property
    def duration_seconds(self) -> float | None:
        """
        How long the drill took, as it was observed: from the moment it began to the moment it
        ended. None when either is missing or unreadable.
        """
        try:
            began = datetime.fromisoformat(self.started_at)
            ended = datetime.fromisoformat(self.finished_at)
        except ValueError:
            return None
        return max((ended - began).total_seconds(), 0.0)

    @property
    def databases_loaded(self) -> int:
        """How many database dumps it loaded into a temporary database and found sound."""
        return sum(
            1
            for step in self.steps
            if step.name not in _NOT_A_DATABASE and step.ok and not step.skipped
        )

    @property
    def summary(self) -> str:
        """One sentence: how it went."""
        failed = [step for step in self.steps if not step.ok]
        if not self.steps:
            return "Nothing was rehearsed."
        if failed:
            return f"{failed[0].name}: {failed[0].detail.splitlines()[0]}"
        tried = [step for step in self.steps if not step.skipped]
        return f"{len(tried)} check(s) passed on {self.backup_id}."

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the report as plain data.

        Returns:
            Every attribute, ``ok`` and ``summary`` included.
        """
        return {
            "id": self.id,
            "domain": self.domain,
            "backup_id": self.backup_id,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
        }


class DrillRecords:
    """
    The store's record of drills.

    Args:
        store: The store holding ``restore_drills``.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store or get_store()

    def save(self, report: DrillReport) -> DrillReport:
        """
        Keep a finished report.

        Args:
            report: The report.

        Returns:
            It, with the id the store gave it.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO restore_drills (app_domain, backup_id, trigger, ok, started_at, "
                "finished_at, report) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    report.domain,
                    report.backup_id,
                    report.trigger,
                    int(report.ok),
                    report.started_at,
                    report.finished_at,
                    json.dumps([step.to_dict() for step in report.steps]),
                ),
            )
            report.id = cursor.lastrowid
        return report

    def list(self, domain: str | None = None, *, limit: int = 20) -> list[DrillReport]:
        """
        List drills, newest first.

        Args:
            domain: Only this application's.
            limit: At most this many.

        Returns:
            The reports.
        """
        query = "SELECT * FROM restore_drills"
        params: list[Any] = []
        if domain is not None:
            query += " WHERE app_domain = ?"
            params.append(domain)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._store._transaction() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return [self._report(row) for row in rows]

    def last_good(self, domain: str) -> DrillReport | None:
        """
        Find the newest drill that passed.

        Args:
            domain: The application.

        Returns:
            The report, or None.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM restore_drills WHERE app_domain = ? AND ok = 1 "
                "ORDER BY id DESC LIMIT 1",
                (domain,),
            )
            row = cursor.fetchone()
        return self._report(row) if row else None

    def evidence(self, domain: str) -> dict[str, Any]:
        """
        Say what is known of the restores tried for an application, as evidence.

        Args:
            domain: The application.

        Returns:
            ``last_ok_at`` (when the newest drill that passed ended), ``duration_s`` (how long that
            one took, as observed), ``databases`` (how many dumps it loaded), ``backup_id``,
            ``last_at`` and ``last_ok`` (the newest attempt, whatever its result) and
            ``last_summary`` (what it found, when it failed). The times and the result are None when
            no drill was ever held.
        """
        good = self.last_good(domain)
        latest = next(iter(self.list(domain, limit=1)), None)
        return {
            "domain": domain,
            "last_ok_at": good.finished_at if good else None,
            "duration_s": good.duration_seconds if good else None,
            "databases": good.databases_loaded if good else 0,
            "backup_id": good.backup_id if good else None,
            "last_at": latest.finished_at if latest else None,
            "last_ok": latest.ok if latest else None,
            "last_summary": latest.summary if latest and not latest.ok else "",
        }

    @staticmethod
    def _report(row: sqlite3.Row) -> DrillReport:
        """
        Build a report from a row.

        Args:
            row: A ``restore_drills`` row.

        Returns:
            The report.
        """
        try:
            steps = [DrillStep(**entry) for entry in json.loads(row["report"])]
        except (ValueError, TypeError):
            steps = []
        return DrillReport(
            domain=row["app_domain"],
            backup_id=row["backup_id"],
            trigger=row["trigger"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            steps=steps,
            id=row["id"],
        )
