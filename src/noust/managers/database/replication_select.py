# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which replication manager serves an engine: the one answer, for ``noust db replica`` and the API.

PostgreSQL's replicas and MySQL's and MariaDB's are two managers with one surface
(:class:`~noust.managers.database.replication_base.ReplicationBase`). Whoever has an engine's manager
and wants its replicas asks here, so that the command line and ``/api/databases/replicas`` choose the
same class and refuse the same engines in the same words (rule 3).
"""

from __future__ import annotations

from noust.core.exceptions import ValidationError
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.replication import ReplicationManager
from noust.managers.database.replication_mysql import MySqlReplication

__all__ = ["REPLICA_ENGINES", "replication_for"]

#: The engines a replica is offered for: PostgreSQL, and MySQL or MariaDB, whichever is installed.
REPLICA_ENGINES = ("postgresql", "mysql")


def replication_for(manager: BaseDatabaseManager) -> ReplicationManager | MySqlReplication:
    """
    Build the replication manager of an engine's manager.

    Args:
        manager: The engine's manager, from the service.

    Returns:
        PostgreSQL's replicas, or MySQL's and MariaDB's.

    Raises:
        ValidationError: When the engine has no replicas to offer (a container's, Redis).
    """
    if isinstance(manager, MySQLManager):
        if manager.instance is not None:
            raise ValidationError(
                "Replicas are for the engines of this server, not the ones Noust runs in containers",
                field="engine",
            )
        return MySqlReplication(manager)
    if isinstance(manager, PostgresManager):
        if manager.instance is not None:
            raise ValidationError(
                "Replicas are for the engines of this server, not the ones Noust runs in containers",
                field="engine",
            )
        return ReplicationManager(manager)
    raise ValidationError(
        f"{manager.DISPLAY_NAME} has no replicas to offer",
        details="Replicas are for PostgreSQL, MySQL and MariaDB.",
        field="engine",
    )
