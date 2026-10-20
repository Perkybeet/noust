# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The backups a recipe's application starts life with.

Coolify's one-click services leave the copy, the update and the test of the
recovery to the operator ("one-click means only the initial Compose"). An
application installed from a recipe here is already protected the day it
exists:

- every database Noust provisioned for it is dumped **daily**, the last 7 dumps
  or 30 days kept, and each dump is **test-restored** into a temporary database
  as proof it loads (:meth:`DatabaseBackups.set_policy` with ``verify_restore``);
- its data (the persistent paths of a native recipe, the named volumes of a
  Compose one) is archived daily, 7 kept, with SQLite files snapshotted whole
  (:meth:`VolumeBackupManager.set_policy`);
- a database that lives **inside** a Compose stack (the 49 recipes that ship their
  PostgreSQL, MariaDB or MongoDB) has no row in the store, so the policy above never
  reaches it and the live copy of its volume "can fail to start". The application's own
  backup schedule is made instead, which dumps each of them through its engine's client
  and, when the restore drill is due (the first night, then every ``backup.drill_days``),
  loads the dump into a throwaway container of the same image.

All are ordinary policies, shown and changed like any: ``noust db
backup-schedule``, ``noust backup volumes schedule``, ``noust backup schedule``.
Nothing here is a second mechanism. The install has already succeeded when this runs, so a policy that
cannot be made is a warning in the notes, never a failed install; and nothing is
sent anywhere else without the operator choosing a destination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.store import App, AppType, NoustStore

if TYPE_CHECKING:
    from noust.managers.stack_databases import StackDatabase

#: What a freshly installed application is backed up with.
DEFAULT_SCHEDULE = "daily"
DEFAULT_KEEP = 7
DEFAULT_DAYS = 30

#: Engines whose dumps cannot be test-restored or are not worth keeping by default.
_NOT_SCHEDULED = frozenset({"redis", "valkey"})


@dataclass(frozen=True)
class DefaultBackups:
    """
    What was scheduled for a new application.

    Attributes:
        databases: The databases given a daily, verified policy (``engine/name``).
        data: Whether its data directories and volumes were given one.
        notes: Sentences for the operator: what is protected, and what could not be.
        stack: The databases inside its Compose stack that its backup schedule dumps.
    """

    databases: tuple[str, ...] = ()
    data: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
    stack: tuple[str, ...] = ()


def apply_default_backups(domain: str, *, logger: Logger, store: NoustStore) -> DefaultBackups:
    """
    Schedule the default backups of an application a recipe just installed.

    Args:
        domain: The application's domain, already deployed.
        logger: Where progress is reported.
        store: The store.

    Returns:
        What was scheduled and what was not, in words. Never raises for a
        policy that could not be made: that is a note.
    """
    app = store.get_app(domain)
    if app is None or app.id is None:
        return DefaultBackups()
    notes: list[str] = []
    protected: list[str] = []
    for database in store.list_databases(app_id=app.id):
        if database.engine in _NOT_SCHEDULED:
            continue
        label = f"{database.engine}/{database.name}"
        try:
            _database_policy(database.engine, database.name, logger)
        except (NoustError, OSError) as exc:
            notes.append(f"No daily backup could be scheduled for {label}: {_said(exc)}")
        else:
            protected.append(label)
    data = False
    try:
        data = _data_policy(domain, logger)
    except (NoustError, OSError) as exc:
        notes.append(f"No daily backup of the data could be scheduled: {_said(exc)}")
    inside = _stack_policy(app, store, notes, logger)
    summary: list[str] = []
    if protected:
        summary.append(
            f"{', '.join(protected)} {'is' if len(protected) == 1 else 'are'} dumped every day "
            f"({DEFAULT_KEEP} kept) and each dump is test-restored."
        )
    if data:
        summary.append(f"Its data is archived every day ({DEFAULT_KEEP} kept).")
    if inside:
        summary.append(
            f"{', '.join(inside)} {'is' if len(inside) == 1 else 'are'} dumped every day "
            f"({DEFAULT_KEEP} kept) with the application's backup, and the dump is loaded into "
            f"a throwaway container of the same image to prove it restores "
            f"('noust backup drill {domain}' does it now)."
        )
    if summary:
        summary.append(
            "These stay on this server until you send them elsewhere: "
            "noust backup destination add, then --to NAME on the schedule."
        )
    return DefaultBackups(tuple(protected), data, tuple(summary + notes), tuple(inside))


def stack_databases_of(app: App) -> list[StackDatabase]:
    """
    Find the databases a Compose application's stack runs.

    The stack is read the way an update reads it (``docker compose config``, then the
    official database images and what ``backup.databases`` of the project file declares).

    Args:
        app: The application, a Compose stack.

    Returns:
        The databases Noust can dump; empty for a stack that runs none.

    Raises:
        NoustError: The stack could not be read.
    """
    from noust.managers.stack_databases import (
        declared_databases,
        detect_stack_databases,
        read_compose_config,
        stack_host_for,
    )

    root = Path(app.app_path)
    host = stack_host_for(app.domain, root)
    return detect_stack_databases(read_compose_config(host), declared_databases(root))


def _stack_policy(app: App, store: NoustStore, notes: list[str], logger: Logger) -> list[str]:
    """
    Make sure the databases inside a Compose stack are dumped every day.

    The application's own backup schedule does it (it dumps each database through its
    engine's client), so an operator's schedule is never replaced: it is said when it
    already covers them, and when it leaves them out.

    Args:
        app: The application.
        store: The store.
        notes: Where what the operator should know is added.
        logger: Where progress is reported.

    Returns:
        The databases that are dumped every day now (``engine/name (service s)``).
    """
    if app.app_type != AppType.DOCKER_COMPOSE.value:
        return []
    try:
        found = stack_databases_of(app)
    except (NoustError, OSError) as exc:
        notes.append(
            "The databases inside its stack could not be read, so none is dumped every day: "
            f"{_said(exc)}"
        )
        return []
    if not found:
        return []
    labels = [database.label for database in found]
    existing = store.get_backup_schedule(app.domain)
    if existing is not None:
        if existing.include_databases:
            notes.append(
                f"{app.domain} already has a backup schedule ({existing.schedule}): it dumps "
                "the databases inside its stack, so none was made."
            )
            return labels
        notes.append(
            f"{app.domain} already has a backup schedule that leaves its databases out: the "
            f"databases inside its stack ({', '.join(labels)}) are not dumped. Change it with: "
            f"noust backup schedule update {app.domain} (the databases are included by default)."
        )
        return []
    from noust.core.utils import domain_to_app_name
    from noust.managers.backup_scheduler import BackupSchedule, BackupScheduler

    try:
        BackupScheduler(verbose=logger.verbose).create_schedule(
            BackupSchedule(
                domain=app.domain,
                app_name=domain_to_app_name(app.domain),
                schedule=DEFAULT_SCHEDULE,
                include_databases=True,
                retention_count=DEFAULT_KEEP,
                retention_days=DEFAULT_DAYS,
            )
        )
    except (NoustError, OSError) as exc:
        notes.append(
            f"No daily backup of the databases inside its stack could be scheduled: {_said(exc)}"
        )
        return []
    return labels


def _said(error: Exception) -> str:
    """
    Say what a refusal said, in one sentence.

    Args:
        error: What a manager raised.

    Returns:
        Its message, with its advice when it has one.
    """
    message = getattr(error, "message", None) or str(error)
    details = getattr(error, "details", None)
    return f"{message} ({details})" if details else str(message)


def _database_policy(engine: str, name: str, logger: Logger) -> None:
    """
    Give one database the default policy.

    Args:
        engine: The engine.
        name: The database.
        logger: Where progress is reported.

    Raises:
        NoustError: The engine or the timer refused it.
    """
    from noust.managers.database.backups import DatabaseBackups
    from noust.managers.database.service import DatabaseService

    DatabaseBackups(DatabaseService(logger=logger)).set_policy(
        engine,
        name,
        schedule=DEFAULT_SCHEDULE,
        retention_count=DEFAULT_KEEP,
        retention_days=DEFAULT_DAYS,
        verify_restore=True,
    )


def _data_policy(domain: str, logger: Logger) -> bool:
    """
    Give the application's data the default policy, when it has any.

    Args:
        domain: The application.
        logger: Where progress is reported.

    Returns:
        Whether a policy was made. False for an application with no data to
        back up, which is not a failure.

    Raises:
        NoustError: The timer could not be made.
    """
    from noust.managers.volume_backups import VolumeBackupManager

    manager = VolumeBackupManager(logger=logger)
    if not manager.targets(domain):
        return False
    manager.set_policy(
        domain,
        schedule=DEFAULT_SCHEDULE,
        retention_count=DEFAULT_KEEP,
        retention_days=DEFAULT_DAYS,
    )
    return True
