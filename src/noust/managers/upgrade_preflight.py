# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What is checked before Noust updates itself, and what stops it (checklist N-26).

An update replaces the program that runs the server as root. Coolify's "complete" after a failed
update, and a store migrated with no way back, are the failures this answers. Each check is a
read:

- **disk space**, where the store lives and where the package is unpacked: an update that runs out
  of room halfway leaves a half-installed Noust;
- **the store reads and is whole** (``PRAGMA quick_check``): a migration of a damaged store is how
  data is lost, and the copy Noust keeps beside the store before migrating
  (``noust.db.v<N>-*.bak``) is only as good as the file it copied;
- **the package manager's own simulation**: ``apt-get -s``, ``dnf`` in test mode, ``zypper
  --dry-run``. A held package, a conflict or a dependency another repository cannot supply is
  reported in the package manager's words *before* anything is installed, and stops the update;
- **a recent self-backup** (``noust self-backup``: the configuration and the store in one file): its
  absence is a warning, not a stop, since the operator may keep their own.

A ``fail`` stops the update with its words (:class:`~noust.managers.self_update.SelfUpdateRefused`);
a ``warn`` is shown and the update goes on. A simulation that could not be run is a warning that
says so: it must not become permission, and must not become a reason to refuse either.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from noust.core.exceptions import NoustError
from noust.core.runner import CommandRunner, get_runner

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "warn", "fail"]

#: Free space below which the update is refused: not enough to unpack the package twice.
MIN_FREE_BYTES = 64 * 1024 * 1024

#: Free space below which the operator is warned.
WARN_FREE_BYTES = 256 * 1024 * 1024

#: How old the newest self-backup may be before it is called out.
BACKUP_MAX_AGE = timedelta(days=7)

#: Seconds the package manager's simulation may take.
SIMULATION_TIMEOUT = 300

#: The last lines of the simulation's output kept as evidence.
EVIDENCE_LINES = 20

#: Errors a check may end in without making the others unreadable.
CHECK_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, sqlite3.Error, ValueError)


@dataclass(frozen=True)
class PreflightCheck:
    """
    One check before an update.

    Attributes:
        name: Stable identifier (``disk``, ``store``, ``packages``, ``self_backup``).
        status: ``ok``, ``warn`` (shown, the update goes on) or ``fail`` (it stops).
        text: What was found, in Noust's words.
        evidence: The tool's own output, verbatim, when it said something.
        fix: What to do about it.
    """

    name: str
    status: CheckStatus
    text: str
    evidence: str = ""
    fix: str | None = None


@dataclass(frozen=True)
class PreflightReport:
    """
    Every check of one update.

    Attributes:
        checks: In the order they ran.
    """

    checks: tuple[PreflightCheck, ...] = field(default_factory=tuple)

    @property
    def blocking(self) -> tuple[PreflightCheck, ...]:
        """The checks that stop the update."""
        return tuple(c for c in self.checks if c.status == "fail")

    @property
    def ready(self) -> bool:
        """True when nothing stops the update."""
        return not self.blocking

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            ``ready`` and each check with its status, text, evidence and fix.
        """
        return {
            "ready": self.ready,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "text": c.text,
                    "evidence": c.evidence,
                    "fix": c.fix,
                }
                for c in self.checks
            ],
        }


def simulation_argv(method: str, names: Sequence[str]) -> list[str] | None:
    """
    The package manager's own rehearsal of the update, which changes nothing.

    Args:
        method: The installation method (``apt``, ``dnf``, ``yum``, ``zypper``...).
        names: The packages the update names.

    Returns:
        The argv, or None for a method with nothing to simulate (pip resolves as it installs).
    """
    if method == "apt":
        return [
            "apt-get",
            "-s",
            "-q",
            "-o",
            "Dpkg::Use-Pty=0",
            "install",
            "--only-upgrade",
            *names,
        ]
    if method == "dnf":
        return ["dnf", "-y", "upgrade", "--refresh", "--setopt=tsflags=test", *names]
    if method == "yum":
        return ["yum", "-y", "update", "--setopt=tsflags=test", *names]
    if method == "zypper":
        return ["zypper", "--non-interactive", "update", "--dry-run", *names]
    return None


class UpgradePreflight:
    """
    Run the checks before Noust updates itself.

    Args:
        method: The installation method.
        names: The packages the update installs.
        runner: Runs the simulation; the process-wide one by default.
        state_dir: Where the store lives (disk space is read there).
        store_path: The store file to check, or None to skip that check.
        free_bytes: Reads the free space of a path; :func:`shutil.disk_usage` by default.
        backup_age: The age of the newest self-backup, or None when there is none; read from
            the self-backup manager by default.
        now: The clock.
    """

    def __init__(
        self,
        *,
        method: str,
        names: Sequence[str] = ("noust",),
        runner: CommandRunner | None = None,
        state_dir: Path | None = None,
        store_path: Path | None = None,
        free_bytes: Callable[[Path], int] | None = None,
        backup_age: Callable[[], timedelta | None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.method = method
        self.names = tuple(names)
        self._runner = runner
        self.state_dir = state_dir
        self.store_path = store_path
        self._free = free_bytes or (lambda path: shutil.disk_usage(path).free)
        self._backup_age = backup_age or self._newest_self_backup_age
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    def run(self) -> PreflightReport:
        """
        Run every check.

        Returns:
            The report; a check that could not be made is a warning saying why.
        """
        return PreflightReport((self._disk(), self._store(), self._packages(), self._self_backup()))

    # ---------------------------------------------------------------- checks

    def _disk(self) -> PreflightCheck:
        """Returns: Whether the places the update writes have room."""
        places = [Path("/usr")]
        if self.state_dir is not None:
            places.append(self.state_dir)
        try:
            smallest = min((self._free(place), place) for place in places if place.exists())
        except (OSError, ValueError) as exc:
            return PreflightCheck("disk", "warn", f"Free space could not be read: {exc}")
        free, place = smallest
        megabytes = free // (1024 * 1024)
        if free < MIN_FREE_BYTES:
            return PreflightCheck(
                "disk",
                "fail",
                f"Only {megabytes} MB are free on {place}: not enough to unpack the update",
                fix="Free some space (noust server storage shows what takes it), then update again.",
            )
        if free < WARN_FREE_BYTES:
            return PreflightCheck(
                "disk", "warn", f"{megabytes} MB are free on {place}: enough, but little"
            )
        return PreflightCheck("disk", "ok", f"{megabytes} MB free on {place}")

    def _store(self) -> PreflightCheck:
        """Returns: Whether the store opens and passes SQLite's quick integrity check."""
        if self.store_path is None or not self.store_path.exists():
            return PreflightCheck("store", "ok", "No store yet: nothing to migrate")
        try:
            connection = sqlite3.connect(f"file:{self.store_path}?mode=ro", uri=True, timeout=10)
            try:
                verdict = connection.execute("PRAGMA quick_check").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            return PreflightCheck(
                "store",
                "fail",
                "The store cannot be read",
                evidence=str(exc),
                fix="Restore it from the last copy (noust self-backup list), then update again.",
            )
        said = str(verdict[0]) if verdict else ""
        if said != "ok":
            return PreflightCheck(
                "store",
                "fail",
                "The store did not pass SQLite's integrity check",
                evidence=said,
                fix="Restore it from the last copy (noust self-backup list); migrating a damaged "
                "store would keep the damage.",
            )
        return PreflightCheck("store", "ok", "The store opens and passes SQLite's integrity check")

    def _packages(self) -> PreflightCheck:
        """Returns: What the package manager's own rehearsal of the update says."""
        argv = simulation_argv(self.method, self.names)
        if argv is None:
            return PreflightCheck(
                "packages", "ok", f"Nothing to rehearse for an installation made with {self.method}"
            )
        result = self.runner.run(argv, timeout=SIMULATION_TIMEOUT)
        output = "\n".join(
            (result.stdout + "\n" + result.stderr).strip().splitlines()[-EVIDENCE_LINES:]
        )
        if result.success:
            return PreflightCheck(
                "packages", "ok", f"{argv[0]} can install the update (rehearsed, nothing changed)"
            )
        return PreflightCheck(
            "packages",
            "fail",
            f"{argv[0]} refuses the update when it rehearses it",
            evidence=output,
            fix="Resolve what it names (a held package, a conflict, a repository that is down), "
            "then update again.",
        )

    def _newest_self_backup_age(self) -> timedelta | None:
        """
        Returns:
            How old the newest self-backup on this server is; None when there is none.

        Raises:
            NoustError: The backups could not be listed.
        """
        from noust.managers.self_backup import SelfBackupManager

        newest: datetime | None = None
        for entry in SelfBackupManager().entries():
            if not entry.created_at:
                continue
            try:
                taken = datetime.fromisoformat(entry.created_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if taken.tzinfo is None:
                taken = taken.replace(tzinfo=timezone.utc)
            if newest is None or taken > newest:
                newest = taken
        return None if newest is None else self._now() - newest

    def _self_backup(self) -> PreflightCheck:
        """Returns: Whether the configuration and the store have a recent copy to go back to."""
        try:
            age = self._backup_age()
        except CHECK_ERRORS as exc:
            logger.debug("Self-backups could not be listed: %s", exc)
            return PreflightCheck("self_backup", "warn", f"Self-backups could not be listed: {exc}")
        advice = "Take one first: noust self-backup create"
        if age is None:
            return PreflightCheck(
                "self_backup",
                "warn",
                "There is no self-backup of the configuration and the store on this server",
                fix=advice,
            )
        if age > BACKUP_MAX_AGE:
            return PreflightCheck(
                "self_backup",
                "warn",
                f"The newest self-backup is {age.days} days old",
                fix=advice,
            )
        return PreflightCheck("self_backup", "ok", "A self-backup from the last week exists")
