# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The retention sweep: every backup policy's limits, applied without waiting for a run.

A policy used to prune its copies only after a run that went well, so a run that failed pruned
nothing and a timer that stopped pruned nothing either, which is how a disk fills while backups
fail. Retention now also runs here, as a job of its own that a daily timer
(``noust-backup-sweep``) starts and ``noust backup sweep`` starts by hand, over the three kinds of
backup that have a policy: database dumps, the data of applications and application backups.

What it shares with a run is the rule (:func:`~noust.managers.retention.select_expired`) and the
scope: a schedule only ever deletes what a schedule made, the newest copy is kept, and when the
newest failed its check the newest good one is kept too. It reads the policies and the files, never
the engines, so it works with an engine down. Destinations are pruned when a run sends to them, as
before: a failed run sends nothing, and a remote store does not fill from a run that failed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from noust.core import audit
from noust.core.exceptions import NoustError

__all__ = ["BackupSweep", "SweepReport"]


@dataclass
class SweepReport:
    """
    What a sweep deleted.

    Attributes:
        databases: For each ``engine/database``, the dumps deleted.
        volumes: For each application, the data archives deleted.
        applications: For each application, how many backups were deleted.
        errors: For each kind that could not be swept, why (the others still were).
    """

    databases: dict[str, list[str]] = field(default_factory=dict)
    volumes: dict[str, list[str]] = field(default_factory=dict)
    applications: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether every kind was swept."""
        return not self.errors

    @property
    def deleted(self) -> int:
        """How many copies were deleted in all."""
        return (
            sum(len(names) for names in self.databases.values())
            + sum(len(ids) for ids in self.volumes.values())
            + sum(self.applications.values())
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The report as plain data, JSON-ready.
        """
        return {
            "ok": self.ok,
            "deleted": self.deleted,
            "databases": self.databases,
            "volumes": self.volumes,
            "applications": self.applications,
            "errors": self.errors,
        }


class BackupSweep:
    """
    Apply every policy's retention.

    Args:
        databases: Sweeps the dumps: returns, per ``engine/database``, what it deleted.
        volumes: Sweeps the data of applications: per application, the ids deleted.
        applications: Sweeps the application backups: per application, how many.
    """

    def __init__(
        self,
        *,
        databases: Callable[[], dict[str, list[str]]] | None = None,
        volumes: Callable[[], dict[str, list[str]]] | None = None,
        applications: Callable[[], dict[str, int]] | None = None,
    ) -> None:
        self._databases = databases
        self._volumes = volumes
        self._applications = applications

    def _sweep_databases(self) -> dict[str, list[str]]:
        """
        Returns:
            What the dumps' sweep deleted.
        """
        if self._databases is not None:
            return self._databases()
        from noust.managers.database.backups import DatabaseBackups
        from noust.managers.database.service import DatabaseService

        return DatabaseBackups(DatabaseService()).sweep_retention()

    def _sweep_volumes(self) -> dict[str, list[str]]:
        """
        Returns:
            What the data archives' sweep deleted.
        """
        if self._volumes is not None:
            return self._volumes()
        from noust.managers.volume_backups import VolumeBackupManager

        return VolumeBackupManager().sweep_retention()

    def _sweep_applications(self) -> dict[str, int]:
        """
        Returns:
            What the application backups' sweep deleted.
        """
        if self._applications is not None:
            return self._applications()
        from noust.managers.backup_scheduler import sweep_application_retention

        return sweep_application_retention()

    def run(self) -> SweepReport:
        """
        Sweep every kind of backup, one failing not stopping the others.

        Returns:
            What was deleted and what could not be swept.
        """
        report = SweepReport()
        try:
            report.databases = self._sweep_databases()
        except (NoustError, OSError) as exc:
            report.errors["databases"] = str(exc)
        try:
            report.volumes = self._sweep_volumes()
        except (NoustError, OSError) as exc:
            report.errors["volumes"] = str(exc)
        try:
            report.applications = self._sweep_applications()
        except (NoustError, OSError) as exc:
            report.errors["applications"] = str(exc)
        audit.record(
            "backups.sweep",
            target="backups",
            outcome="ok" if report.ok else "failure",
            details={
                "deleted": report.deleted,
                "databases": len(report.databases),
                "volumes": len(report.volumes),
                "applications": len(report.applications),
                "errors": sorted(report.errors),
            },
        )
        return report
