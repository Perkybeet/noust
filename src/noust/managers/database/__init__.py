# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database managers package for Noust.

Provides managers for different database engines:
- MySQL/MariaDB
- PostgreSQL
- Redis/Valkey
- MongoDB

The service every front end calls is
:class:`noust.managers.database.service.DatabaseService`; it is imported from
its own module, not from here, because it depends on the deployers' helpers,
which depend on this package.
"""

from noust.managers.database.base import PROFILES, BaseDatabaseManager, DatabaseInfo, UserInfo
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager
from noust.managers.database.registry import DatabaseRegistry, get_db_manager

__all__ = [
    "PROFILES",
    "BaseDatabaseManager",
    "DatabaseInfo",
    "DatabaseRegistry",
    "MongoDBManager",
    "MySQLManager",
    "PostgresManager",
    "RedisManager",
    "UserInfo",
    "get_db_manager",
]
