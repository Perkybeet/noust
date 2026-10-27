# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deployers for Noust."""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from noust.deployers.base import BaseDeployer
    from noust.deployers.docker_compose import DockerComposeDeployer
    from noust.deployers.monorepo import MonorepoDeployer
    from noust.deployers.registry import DeployerRegistry, detect_app_type, get_deployer

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "BaseDeployer": "noust.deployers.base",
        "DockerComposeDeployer": "noust.deployers.docker_compose",
        "MonorepoDeployer": "noust.deployers.monorepo",
        "DeployerRegistry": "noust.deployers.registry",
        "detect_app_type": "noust.deployers.registry",
        "get_deployer": "noust.deployers.registry",
    },
)

__all__ = [
    "BaseDeployer",
    "DeployerRegistry",
    "DockerComposeDeployer",
    "MonorepoDeployer",
    "detect_app_type",
    "get_deployer",
]
