# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A restore drill: prove the newest backup of an application comes back, without bringing it back.

A backup nobody has restored is a hope. ``noust backup verify`` proves an
archive is readable and unpackable; a drill goes on to what a bad day needs:

1. the backup's archive is **verified in depth** (checksum, signature, and the
   extractor a restore uses);
2. it is **unpacked** into a scratch directory and looked at (what application
   it holds, how many files);
3. **each database dump it carries is loaded into a temporary database** of the
   engine it came from, looked at, and dropped (the same test ``noust db
   backup-verify --restore-test`` runs, on the dumps inside the application's
   archive);
4. a **dated report** is kept and, when something fails, said.

What a drill never does: touch the application, its databases, its units or its
site, or start a second copy of the application. It says so in its report: the
files are proven to unpack and the data to load, not that a new server would
serve them (``noust restore-server --plan`` rehearses that part).

A drill runs by hand (``noust backup drill DOMAIN``), and a scheduled backup runs
one when the last good drill is older than ``backup.drill_days`` (30 by default; 0
turns it off), after the backup, in the same run. Its reports are rows of
``restore_drills`` (``schema_v14.BACKUPS_SQL``).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.core import audit
from noust.core.audit import Actor
from noust.core.config import Config
from noust.core.exceptions import DatabaseError, NoustError
from noust.core.logger import Logger
from noust.core.store import NoustStore, get_store
from noust.managers import sqlite_files
from noust.managers.backup_manager import BackupManager

#: How a drill was started.
TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULE = "schedule"

#: How many days between the drills a schedule runs, when ``backup.drill_days`` is unset.
DEFAULT_DRILL_DAYS = 30

#: Engines whose snapshot replaces the whole instance and cannot be tried on the side.
_UNTESTABLE = frozenset({"redis", "valkey"})


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def drill_days(config: Config | None = None) -> int:
    """
    Say how often a scheduled backup is followed by a drill.

    Args:
        config: The configuration; the default one when None.

    Returns:
        ``backup.drill_days`` (30 when unset); 0 for never.
    """
    try:
        return max(int((config or Config()).get("backup.drill_days", DEFAULT_DRILL_DAYS)), 0)
    except (TypeError, ValueError):
        return DEFAULT_DRILL_DAYS


def drill_due(
    domain: str, *, store: NoustStore | None = None, config: Config | None = None
) -> bool:
    """
    Tell whether an application is due a drill.

    Args:
        domain: The application.
        store: The store.
        config: The configuration.

    Returns:
        True when drills are on and none passed within ``backup.drill_days``.
    """
    days = drill_days(config)
    if days <= 0:
        return False
    last = DrillRecords(store).last_good(domain)
    if last is None or not last.finished_at:
        return True
    try:
        finished = datetime.fromisoformat(last.finished_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - finished > timedelta(days=days)


class RestoreDrill:
    """
    Run restore drills.

    Args:
        backups: The application backup manager.
        logger: Where progress is reported.
        store: The store.
        database_service: Builds the database service a dump is tried through;
            the real one by default.
        actor: Who runs it, for the audit trail.
    """

    def __init__(
        self,
        backups: BackupManager | None = None,
        *,
        logger: Logger | None = None,
        store: NoustStore | None = None,
        database_service: Callable[[], Any] | None = None,
        actor: Actor | None = None,
    ) -> None:
        self.logger = logger or Logger()
        self.backups = backups or BackupManager()
        self.store = store or get_store()
        self._service = database_service
        self.actor = actor

    def run(
        self,
        domain: str,
        *,
        backup_id: str | None = None,
        trigger: str = TRIGGER_MANUAL,
        on_step: Callable[[str], None] | None = None,
    ) -> DrillReport:
        """
        Rehearse restoring an application's backup, and keep the report.

        Never raises for what it finds: a failed check is the answer. It raises
        only for what makes a drill impossible to record.

        Args:
            domain: The application.
            backup_id: The backup to rehearse; the newest one when None.
            trigger: ``manual`` or ``schedule``.
            on_step: Told what is happening, for a job's progress.

        Returns:
            The report: ``ok`` when everything that was tried passed.
        """
        say = on_step or (lambda text: None)
        report = DrillReport(domain, backup_id, trigger, _now())
        try:
            self._rehearse(report, say)
        except NoustError as exc:
            report.steps.append(
                DrillStep("drill", False, f"{exc.message}\n{exc.details or ''}".strip())
            )
        report.finished_at = _now()
        DrillRecords(self.store).save(report)
        audit.record(
            "restore_drill.run",
            actor=self.actor,
            target=f"app:{domain}",
            outcome="ok" if report.ok else "failure",
            details={
                "backup": report.backup_id,
                "trigger": trigger,
                "steps": [step.to_dict() for step in report.steps][:12],
            },
        )
        return report

    def _rehearse(self, report: DrillReport, say: Callable[[str], None]) -> None:
        """
        Do the steps, adding what each finds to the report.

        Args:
            report: The report being made.
            say: Reports progress.
        """
        chosen = (
            self.backups.get_backup(report.backup_id)
            if report.backup_id
            else self.backups.get_latest_backup(report.domain)
        )
        if chosen is None or chosen.domain != report.domain:
            report.steps.append(
                DrillStep(
                    "backup",
                    False,
                    "There is no backup to rehearse"
                    + (
                        f": {report.backup_id} is not one of {report.domain}'s"
                        if report.backup_id
                        else ""
                    )
                    + ". Take one with: noust backup create "
                    + report.domain,
                )
            )
            return
        report.backup_id = chosen.id
        report.steps.append(DrillStep("backup", True, f"{chosen.id} ({chosen.size_human})"))

        say(f"Verifying {chosen.id}")
        verdict = self.backups.verify(chosen.id, deep=True)
        if not verdict.get("valid"):
            report.steps.append(
                DrillStep("archive", False, "; ".join(verdict.get("errors") or ["not valid"]))
            )
            return
        report.steps.append(
            DrillStep(
                "archive",
                True,
                f"checksum, signature and extraction pass ({verdict.get('file_count', 0)} files)",
            )
        )

        say("Unpacking it")
        with self.backups.unpacked(chosen.id) as unpacked:
            files = sum(1 for path in unpacked.root.rglob("*") if path.is_file())
            report.steps.append(
                DrillStep("unpack", True, f"{files} files unpacked with a restore's own rules")
            )
            self._sqlite(report, unpacked.root, say)
            self._databases(report, unpacked.dumps, unpacked.stack_dumps, say)
        report.steps.append(
            DrillStep(
                "application",
                True,
                "The application itself was not started: a drill proves the files unpack and "
                "the data loads. Rehearse a whole server with: noust restore-server --plan",
                skipped=True,
            )
        )

    def _sqlite(self, report: DrillReport, root: Path, say: Callable[[str], None]) -> None:
        """
        Ask SQLite whether each database kept in the unpacked files is sound.

        The files of an application that keeps its data in SQLite (a Vaultwarden, a Memos) are
        its database: a copy SQLite cannot read is a backup that does not bring the data back.
        It adds nothing for an application that keeps none.

        Args:
            report: The report being made.
            root: The unpacked backup.
            say: Reports progress.
        """
        found = sqlite_files.check_tree(root)
        if found:
            say(f"Checking {len(found)} SQLite database(s)")
        for result in found:
            if result.problems:
                detail = "\n".join(result.problems)
            elif not result.complete:
                detail = (
                    f"SQLite found nothing wrong up to where its {result.seconds:g} s ended; "
                    "run 'noust db sqlite check --full' on a restored copy for the whole file"
                )
            else:
                detail = f"SQLite's quick_check passes ({result.seconds:g} s)"
            report.steps.append(DrillStep(f"sqlite/{result.path}", result.ok, detail))

    def _databases(
        self,
        report: DrillReport,
        dumps: tuple[tuple[str, str, Any], ...],
        stack_dumps: int,
        say: Callable[[str], None],
    ) -> None:
        """
        Load each database dump into a temporary database.

        Args:
            report: The report being made.
            dumps: ``(engine, database, file)`` for each dump the backup carries.
            stack_dumps: How many dumps of a Compose stack's own databases it
                carries, which cannot be tried on the side.
            say: Reports progress.
        """
        from noust.managers.database.backup_verify import restore_test

        if stack_dumps:
            report.steps.append(
                DrillStep(
                    "stack databases",
                    True,
                    f"{stack_dumps} dump(s) of the stack's own databases are checked with the "
                    "archive but loaded only by a real restore (they live in containers)",
                    skipped=True,
                )
            )
        if not dumps:
            return
        service = self._database_service()
        for engine, database, path in dumps:
            label = f"{engine}/{database}"
            say(f"Loading {label} into a temporary database")
            try:
                manager = service.running(engine)
            except NoustError as exc:
                report.steps.append(
                    DrillStep(label, False, f"{exc.message} {exc.details or ''}".strip())
                )
                continue
            if manager.engine_type in _UNTESTABLE:
                report.steps.append(
                    DrillStep(
                        label,
                        True,
                        "a snapshot replaces every key of the instance, so it cannot be loaded "
                        "on the side; its file is checked with the archive",
                        skipped=True,
                    )
                )
                continue
            try:
                outcome = restore_test(manager, path)
            except (DatabaseError, OSError) as exc:
                report.steps.append(DrillStep(label, False, str(exc)))
                continue
            report.steps.append(DrillStep(label, outcome.ok, outcome.detail))

    def _database_service(self) -> Any:
        """
        Build the database service dumps are tried through.

        Returns:
            The service.
        """
        if self._service is not None:
            return self._service()
        from noust.managers.database.service import DatabaseService

        return DatabaseService(logger=self.logger)
