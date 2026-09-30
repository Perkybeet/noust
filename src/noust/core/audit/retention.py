# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Retention of what Noust records about its own work (ENS G19, op.exp.8.r3).

Data kept "just in case" is data that leaks: a job log holds build output, a
deployment row names who deployed what and when, the monitor's observations
name processes. ENS asks for what is necessary and no more, so each record
has a period (``retention.*``, ``monitor.retention_days`` for observations)
and :func:`prune` deletes what is past it, then says so in the audit log
(``retention.prune``). The audit log itself is kept by
:meth:`~noust.core.audit.log.AuditLog.purge` with its own rules: by age, and
never before every destination has received it.

Console sessions live in the console's own store; the console registers how
to prune them with :func:`register_pruner`, so this module does not reach
into the web layer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.audit.actor import Actor
from noust.core.audit.settings import RetentionSettings, load_retention
from noust.core.exceptions import NoustError
from noust.core.fs import get_fs, is_rehearsal

if TYPE_CHECKING:
    from noust.core.store import NoustStore

logger = logging.getLogger(__name__)

#: Record kind to a function deleting what is older than a cutoff and
#: returning how many it deleted.
_pruners: dict[str, Callable[[datetime], int]] = {}


def register_pruner(kind: str, pruner: Callable[[datetime], int]) -> None:
    """
    Add a kind of record :func:`prune` keeps within its retention period.

    Args:
        kind: ``sessions``, or another ``retention.<kind>_days`` setting.
        pruner: Deletes records older than the cutoff it is given (a local
            naive datetime) and returns how many.
    """
    _pruners[kind] = pruner


def unregister_pruner(kind: str) -> None:
    """
    Remove a pruner added with :func:`register_pruner`.

    Args:
        kind: Its kind.
    """
    _pruners.pop(kind, None)


@dataclass
class RetentionReport:
    """
    What one pass deleted.

    Attributes:
        deleted: Record kind to how many were deleted.
        files: Log files deleted with their records.
        errors: Kinds that could not be pruned, with the reason.
    """

    deleted: dict[str, int] = field(default_factory=dict)
    files: int = 0
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        The report as JSON-ready data.

        Returns:
            Every field.
        """
        return {"deleted": dict(self.deleted), "files": self.files, "errors": dict(self.errors)}


def _delete_logs(paths: list[str], root: Path) -> int:
    """
    Delete log files that belong to a directory Noust owns.

    A path read back from the store is not trusted to point where Noust put
    it: only regular files inside ``root`` are deleted, never a link, so a
    row edited to name ``/etc/shadow`` deletes nothing.

    Args:
        paths: Paths from the deleted rows.
        root: The directory those logs live in.

    Returns:
        How many files were deleted.
    """
    fs = get_fs()
    deleted = 0
    base = root.resolve()
    for text in paths:
        path = Path(text)
        if path.is_symlink() or not path.is_file():
            continue
        try:
            path.resolve().relative_to(base)
        except ValueError:
            logger.warning("Not deleting %s: it is outside %s", path, root)
            continue
        fs.remove(path)
        deleted += 1
    return deleted


def prune(
    settings: RetentionSettings | None = None, *, now: datetime | None = None
) -> RetentionReport:
    """
    Delete Noust's records that are past their retention period.

    Args:
        settings: The periods; read from the configuration when None.
        now: The current local time; for tests.

    Returns:
        What was deleted. A rehearsal deletes nothing and reports nothing.
    """
    from noust.core.audit import get_log
    from noust.core.store import get_store

    report = RetentionReport()
    if is_rehearsal():
        return report
    settings = settings or load_retention()
    moment = now or datetime.now()
    store = get_store()
    base = store.db_path.parent

    try:
        count, logs = store.prune_finished_jobs(
            (moment - timedelta(days=settings.jobs_days)).isoformat()
        )
        report.deleted["jobs"] = count
        report.files += _delete_logs(logs, base / "job-logs")
    except (NoustError, OSError) as exc:
        report.errors["jobs"] = str(exc)
    try:
        count, logs = store.prune_deployments_before(
            (moment - timedelta(days=settings.deployments_days)).isoformat(),
            keep=_deployments_still_needed(store),
        )
        report.deleted["deployments"] = count
        report.files += _delete_logs(logs, base / "deploy-logs")
    except (NoustError, OSError) as exc:
        report.errors["deployments"] = str(exc)
    try:
        report.deleted["observations"] = _prune_observations(settings.observations_days)
    except (NoustError, OSError) as exc:
        report.errors["observations"] = str(exc)
    periods = {"sessions": settings.sessions_days}
    for kind, pruner in sorted(_pruners.items()):
        days = periods.get(kind, settings.jobs_days)
        try:
            report.deleted[kind] = pruner(moment - timedelta(days=days))
        except (NoustError, OSError) as exc:
            report.errors[kind] = str(exc)

    if any(report.deleted.values()) or report.errors:
        get_log().append(
            "retention.prune",
            actor=Actor.system("retention"),
            outcome="failure" if report.errors else "ok",
            details={
                **report.to_dict(),
                "periods": {
                    "jobs_days": settings.jobs_days,
                    "deployments_days": settings.deployments_days,
                    "observations_days": settings.observations_days,
                    "sessions_days": settings.sessions_days,
                },
            },
        )
    return report


def _deployments_still_needed(store: NoustStore) -> set[int]:
    """
    Name the deployment rows an application still depends on, whatever their age.

    An application not deployed in a year still runs its last deployment:
    its row is the rollback target, and the newest rows of each application
    are what keeps their snapshot backups from being rotated away
    (:meth:`~noust.managers.backup_manager.BackupManager._protected_backup_ids`
    reads the same :data:`SNAPSHOT_DEPLOYMENTS_KEPT`). An application's
    newest success is kept too, behind any run of failures.

    Args:
        store: The store.

    Returns:
        The ids of the rows the retention period must not delete.
    """
    from noust.core.store import DeploymentStatus
    from noust.managers.backup_manager import SNAPSHOT_DEPLOYMENTS_KEPT

    success = DeploymentStatus.SUCCESS.value
    keep: set[int] = set()
    for app in store.list_apps():
        newest = store.list_deployments(app.domain, limit=SNAPSHOT_DEPLOYMENTS_KEPT)
        keep.update(record.id for record in newest if record.id is not None)
        if not any(record.status == success for record in newest):
            for record in store.list_deployments(app.domain, limit=1000):
                if record.status == success and record.id is not None:
                    keep.add(record.id)
                    break
    return keep


def _prune_observations(days: int) -> int:
    """
    Delete the monitor's observations older than its retention period.

    The monitor prunes on every scan; this covers a server where it is not
    running. A database that does not exist is not created to be pruned.

    Args:
        days: ``monitor.retention_days``.

    Returns:
        Rows deleted.
    """
    from noust.monitor.observation_store import ObservationStore, default_db_path

    path = default_db_path()
    if not path.exists():
        return 0
    return ObservationStore(db_path=path).purge_older_than(days)
