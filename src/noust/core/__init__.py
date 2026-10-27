# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Core modules for Noust.

The names below are loaded when they are read, not when a module of the package is imported:
importing ``noust.core.fs`` runs this file, and an eager list of re-exports would load the
configuration, the store with its migrations and the notifier (ninety modules) into every command
line that only needs a path or an error class (``noust --version`` is one). ``from noust.core
import Config`` is unchanged; see :mod:`noust.core.lazy_exports`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from noust.core.config import Config
    from noust.core.exceptions import NoustError
    from noust.core.logger import Logger
    from noust.core.notifier import NotificationEvent, Notifier
    from noust.core.store import (
        App,
        AppStatus,
        AppType,
        Database,
        DatabaseEngine,
        DatabaseUser,
        MonorepoWorkspace,
        NoustStore,
        Service,
        Site,
        WebServer,
        get_store,
    )

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "Config": "noust.core.config",
        "Logger": "noust.core.logger",
        "NoustError": "noust.core.exceptions",
        "NotificationEvent": "noust.core.notifier",
        "Notifier": "noust.core.notifier",
        **dict.fromkeys(
            (
                "App",
                "AppStatus",
                "AppType",
                "Database",
                "DatabaseEngine",
                "DatabaseUser",
                "MonorepoWorkspace",
                "NoustStore",
                "Service",
                "Site",
                "WebServer",
                "get_store",
            ),
            "noust.core.store",
        ),
    },
)

__all__ = [
    "App",
    "AppStatus",
    "AppType",
    "Config",
    "Database",
    "DatabaseEngine",
    "DatabaseUser",
    "Logger",
    "MonorepoWorkspace",
    "NotificationEvent",
    "Notifier",
    "NoustError",
    "NoustStore",
    "Service",
    "Site",
    "WebServer",
    "get_store",
]
