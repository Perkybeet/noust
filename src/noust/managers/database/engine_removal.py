# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What removing a database engine owes the databases it holds, and the record of it.

Removing an engine takes its packages out, and with ``purge`` its data directory. Whoever asks (the
console's dialog, ``noust db uninstall``, a job, an API client) goes through
:meth:`~noust.managers.database.base.BaseDatabaseManager.uninstall`, so the refusal lives here and
not in the dialog that asked the operator to type the engine's name (rule 4).

The refusal is not a flag to override. An engine holds databases when the store tracks one for it
or when the engine itself lists one, and it is asked only while it runs: nothing can be asked of a
stopped engine, and the store is then all that is known. An engine that runs and cannot be read
(it refuses Noust's account, say) is refused as well, because not knowing what it holds is not
knowing that it holds nothing.

The event is recorded here too, when the removal finishes, fails or is refused: the route that
queued the job used to record ``db.uninstall`` at the moment of queueing, so a job that failed an
hour later still read as an engine removed.

This module sits beside ``base.py``, which is past the size limit and does not grow.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from noust.core import audit
from noust.core.exceptions import DatabaseEngineError, DatabaseError, EngineInUseError, NoustError
from noust.core.package_family import PackageFamily
from noust.core.store import get_store
from noust.managers.database import flavours
from noust.managers.database.rpm_flavours import (
    installed_packages,
    key_file_of,
    repository_files,
)

if TYPE_CHECKING:
    from noust.managers.database.base import BaseDatabaseManager

#: The audit event of removing an engine.
EVENT = "db.uninstall"

#: Deadline of the package manager removing an engine's packages.
PACKAGE_TIMEOUT = 900


def databases_held(manager: BaseDatabaseManager) -> list[str]:
    """
    Name the databases an engine holds: the store's and the engine's own.

    Args:
        manager: The host's engine manager.

    Returns:
        Every name once, sorted; empty when it holds none.

    Raises:
        EngineInUseError: When the engine runs and cannot be asked what it holds.
    """
    names = {row.name for row in get_store().list_databases(engine=manager.ENGINE_NAME)}
    if manager.is_installed() and manager.is_running():
        try:
            names.update(info.name for info in manager.list_databases())
        except DatabaseError as exc:
            raise EngineInUseError(
                f"{manager.DISPLAY_NAME} is running, and Noust cannot say what it holds",
                details=(
                    f"{exc.message}\n{exc.details}\n"
                    "Removing it without knowing what it holds could delete databases: make it "
                    "readable again, then try again."
                ).replace("\n\n", "\n"),
                output=exc.output,
            ) from exc
    return sorted(names)


def require_no_databases(manager: BaseDatabaseManager, *, purge: bool = False) -> None:
    """
    Refuse the removal of an engine that holds databases, and record the refusal.

    Args:
        manager: The host's engine manager.
        purge: Whether the removal would also delete the data directory, for the record.

    Raises:
        EngineInUseError: When the engine holds databases, or cannot be asked.
    """
    try:
        held = databases_held(manager)
    except EngineInUseError as exc:
        _record(manager, purge=purge, outcome="denied", error=exc)
        raise
    if not held:
        return
    noun = "database" if len(held) == 1 else "databases"
    error = EngineInUseError(
        f"{manager.DISPLAY_NAME} still holds {len(held)} {noun}: {', '.join(held)}",
        details=(
            "Removing the engine would delete them or leave them with no server. Drop each one "
            "first (a last dump is kept), or move them to another server:\n"
            f"  noust db drop {held[0]} --engine {manager.ENGINE_NAME}\n"
            "Then remove the engine."
        ),
    )
    _record(manager, purge=purge, outcome="denied", error=error, databases=held)
    raise error


def removal(manager: BaseDatabaseManager, *, purge: bool, remove: Callable[[], None]) -> None:
    """
    Remove an engine behind the guard, and record how it went.

    Args:
        manager: The host's engine manager.
        purge: Whether the data directory goes too.
        remove: What takes the packages out, once the guard has said yes.

    Raises:
        EngineInUseError: When the engine holds databases.
        NoustError: Whatever ``remove`` raised, after it was recorded.
    """
    require_no_databases(manager, purge=purge)
    try:
        remove()
    except (NoustError, OSError) as exc:
        _record(manager, purge=purge, outcome="failure", error=exc)
        raise
    _record(manager, purge=purge, outcome="ok")


def _record(
    manager: BaseDatabaseManager,
    *,
    purge: bool,
    outcome: str,
    error: Exception | None = None,
    databases: list[str] | None = None,
) -> None:
    """
    Write the event, as whoever is acting now (the bound actor of the command or job).

    Args:
        manager: The engine's manager.
        purge: Whether the data directory goes too.
        outcome: ``ok``, ``failure`` or ``denied``.
        error: What stopped it, for a failure or a refusal.
        databases: The databases that held it back.
    """
    details: dict[str, object] = {"purge": purge}
    if databases:
        details["databases"] = databases
    if error is not None:
        details["reason"] = error.message if isinstance(error, NoustError) else str(error)
    audit.record(EVENT, target=f"db:{manager.ENGINE_NAME}", outcome=outcome, details=details)


def remove_rpm_packages(manager: BaseDatabaseManager, family: PackageFamily) -> None:
    """
    Remove the engine's packages with dnf or zypper.

    The packages are the ones installed whose name is exactly one of the flavour's
    (:func:`~noust.managers.database.rpm_flavours.installed_packages`), never a pattern handed
    to the package manager.

    Args:
        manager: The host's engine manager.
        family: The package manager.

    Raises:
        DatabaseEngineError: When no package of the flavour is installed, or the package
            manager fails.
    """
    flavour = manager.installed_flavour() or manager.ENGINE_NAME
    backend = manager._package_backend()
    listing = manager._exec(["rpm", "-qa", "--qf", "%{NAME}\\n"], env=backend.env(), timeout=60)
    names = installed_packages(flavour, listing.stdout) if listing.success else []
    if not names:
        raise DatabaseEngineError(
            f"No package of {manager.DISPLAY_NAME} is installed",
            details="'rpm -qa' lists none of its packages. Nothing was removed.",
        )
    argv = (
        ["dnf", "-y", "remove", *names]
        if family is PackageFamily.DNF
        else ["zypper", "--non-interactive", "remove", *names]
    )
    result = manager._exec(argv, env=backend.env(), timeout=PACKAGE_TIMEOUT)
    if not result.success:
        raise DatabaseEngineError(
            f"Failed to remove {manager.DISPLAY_NAME}",
            details=(result.stderr.strip() or "The package manager returned no output.")[-2000:],
        )
    # The repositories Noust added for this flavour go with it: left enabled, MariaDB's
    # packages, which also provide `mysql-server`, would answer a later install of MySQL.
    for definition in repository_files(flavour, host=flavours.HOST):
        manager.fs.remove(definition)
        manager.fs.remove(flavours.HOST.at(str(key_file_of(definition))))
