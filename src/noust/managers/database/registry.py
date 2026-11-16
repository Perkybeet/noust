# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database manager registry for Noust.

Provides registration and lookup of database engine managers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from noust.managers.database.base import BaseDatabaseManager

if TYPE_CHECKING:
    from noust.managers.database.instances import DatabaseInstance


class DatabaseRegistry:
    """
    Registry for database engine managers.

    Manages registration and retrieval of database managers.
    """

    _managers: ClassVar[dict[str, type[BaseDatabaseManager]]] = {}
    _aliases: ClassVar[dict[str, str]] = {}

    @classmethod
    def register(
        cls,
        manager_class: type[BaseDatabaseManager],
        aliases: list[str] | None = None,
    ) -> None:
        """
        Register a database manager.

        Args:
            manager_class: Database manager class.
            aliases: Optional list of aliases.
        """
        engine_name = manager_class.ENGINE_NAME.lower()
        cls._managers[engine_name] = manager_class

        if aliases:
            for alias in aliases:
                cls._aliases[alias.lower()] = engine_name

    @classmethod
    def get(cls, engine: str, verbose: bool = False) -> BaseDatabaseManager | None:
        """
        Get a database manager instance by engine name.

        Args:
            engine: Engine name or alias.
            verbose: Enable verbose logging.

        Returns:
            Database manager instance or None.
        """
        engine_lower = engine.lower()

        # Check aliases first
        if engine_lower in cls._aliases:
            engine_lower = cls._aliases[engine_lower]

        manager_class = cls._managers.get(engine_lower)
        if manager_class:
            return manager_class(verbose=verbose)

        return None

    @classmethod
    def canonical(cls, engine: str) -> str | None:
        """
        Resolve an engine name or alias to the name it is registered under.

        Args:
            engine: Engine name or alias (``pg``, ``mariadb``, ``valkey``).

        Returns:
            The registered name, or None for an unknown engine.
        """
        name = engine.lower()
        name = cls._aliases.get(name, name)
        return name if name in cls._managers else None

    @classmethod
    def bound(cls, instance: DatabaseInstance, verbose: bool = False) -> BaseDatabaseManager | None:
        """
        Get a manager that drives a database container.

        Args:
            instance: The container, as discovery found it.
            verbose: Enable verbose logging.

        Returns:
            The engine's manager, bound to the container, or None when no
            manager speaks its engine.
        """
        manager = cls.get(instance.engine, verbose)
        return manager.bind(instance) if manager is not None else None

    @classmethod
    def list_engines(cls) -> list[str]:
        """
        List all registered database engines.

        Returns:
            List of engine names.
        """
        return list(cls._managers.keys())

    @classmethod
    def get_all_managers(cls, verbose: bool = False) -> list[BaseDatabaseManager]:
        """
        Get instances of all registered managers.

        Args:
            verbose: Enable verbose logging.

        Returns:
            List of manager instances, one per registered engine.
        """
        # get() is typed as optional because it also resolves user input; here
        # the names come from the registry itself, so nothing can be missing.
        managers = (cls.get(engine, verbose) for engine in cls.list_engines())
        return [
            manager for manager in managers if manager is not None and not manager.CONTAINER_ONLY
        ]

    @classmethod
    def get_installed(cls, verbose: bool = False) -> list[BaseDatabaseManager]:
        """
        Get managers for installed database engines.

        Args:
            verbose: Enable verbose logging.

        Returns:
            List of manager instances for installed engines.
        """
        installed = []
        for engine in cls.list_engines():
            manager = cls.get(engine, verbose)
            if manager and manager.is_installed():
                installed.append(manager)
        return installed


def host_engines(
    injected: Callable[[], list[str]] | None, registered: Callable[[], list[str]]
) -> list[str]:
    """
    List the engines of the system this Noust runs.

    Args:
        injected: The names a caller fixed (a service built around a few engines); they win.
        registered: Where the registry's own names come from.

    Returns:
        The injected names, or the registered engines, or none where this Noust's role does not run the
        system's own databases: the central of a NAS drives the containers it makes through Docker and
        has no PostgreSQL of its own, and says so with the table's sentence
        (:mod:`noust.managers.database.system_engines`) instead of listing engines it lacks.
    """
    if injected is not None:
        return injected()
    from noust import central

    return [] if central.refused_by_role("databases") is not None else registered()


def get_db_manager(engine: str, verbose: bool = False) -> BaseDatabaseManager | None:
    """
    Convenience function to get a database manager.

    Args:
        engine: Engine name or alias.
        verbose: Enable verbose logging.

    Returns:
        Database manager instance or None.
    """
    return DatabaseRegistry.get(engine, verbose)
