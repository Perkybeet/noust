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
  (:meth:`VolumeBackupManager.set_policy`).

Both are ordinary policies, shown and changed like any: ``noust db
backup-schedule``, ``noust backup volumes schedule``. Nothing here is a second
mechanism. The install has already succeeded when this runs, so a policy that
cannot be made is a warning in the notes, never a failed install; and nothing is
sent anywhere else without the operator choosing a destination.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.store import NoustStore

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
    """

    databases: tuple[str, ...] = ()
    data: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


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
    summary: list[str] = []
    if protected:
        summary.append(
            f"{', '.join(protected)} {'is' if len(protected) == 1 else 'are'} dumped every day "
            f"({DEFAULT_KEEP} kept) and each dump is test-restored."
        )
    if data:
        summary.append(f"Its data is archived every day ({DEFAULT_KEEP} kept).")
    if summary:
        summary.append(
            "These stay on this server until you send them elsewhere: "
            "noust backup destination add, then --to NAME on the schedule."
        )
    return DefaultBackups(tuple(protected), data, tuple(summary + notes))


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
