# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Helper modules for deployers.

These modules extract common functionality from BaseDeployer
to improve maintainability and testability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from noust.deployers.helpers.env_manager import EnvManager
    from noust.deployers.helpers.nginx_config import NginxConfigBuilder
    from noust.deployers.helpers.package_manager import PackageManagerHelper
    from noust.deployers.helpers.path_resolver import PathResolver
    from noust.deployers.helpers.prisma import PrismaHelper
    from noust.deployers.helpers.turbo import TurboHelper
    from noust.deployers.helpers.workspace import WorkspaceHelper

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "EnvManager": "noust.deployers.helpers.env_manager",
        "NginxConfigBuilder": "noust.deployers.helpers.nginx_config",
        "PackageManagerHelper": "noust.deployers.helpers.package_manager",
        "PathResolver": "noust.deployers.helpers.path_resolver",
        "PrismaHelper": "noust.deployers.helpers.prisma",
        "TurboHelper": "noust.deployers.helpers.turbo",
        "WorkspaceHelper": "noust.deployers.helpers.workspace",
    },
)

__all__ = [
    "EnvManager",
    "NginxConfigBuilder",
    "PackageManagerHelper",
    "PathResolver",
    "PrismaHelper",
    "TurboHelper",
    "WorkspaceHelper",
]
