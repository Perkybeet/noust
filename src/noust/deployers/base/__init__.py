# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Base deployer class for Noust.

Defines the interface and common functionality for all deployers.

``deploy()`` is a pipeline description, not an implementation: each step names
the manager that does the work and the undo that reverses it. Everything that
belongs to a manager (nginx, systemd, certbot) or to the store lives there, not
here.

Three paths, because the release layout pulls apart what the in-place layout
kept in one directory:

- :attr:`BaseDeployer.app_path` is the application directory, the one the
  store records. In place it is also where the code is built and run.
- :attr:`BaseDeployer.build_path` is where the code being deployed is built:
  the application directory in place, the new release directory on releases.
  Everything that reads or builds the project uses it.
- :attr:`BaseDeployer.runtime_path` is what the unit and the web server are
  given: the application directory in place, ``current`` on releases, so the
  same unit and site serve every release that is activated after them.

In place, all three are the same directory, which is what keeps that layout
exactly as it was.
"""

from __future__ import annotations

from noust.deployers.base.deploy import DeployMixin
from noust.deployers.base.state import (
    BUILD_TIMEOUT,
    COMMAND_TIMEOUT,
    GIT_LOG_TIMEOUT,
    INSTALL_TIMEOUT,
    PackageManager,
)


class BaseDeployer(DeployMixin):
    """
    Abstract base class for application deployers.

    Each deployer handles the deployment workflow for a specific
    type of application (Next.js, Node.js, Python, etc.).
    """


__all__ = [
    "BUILD_TIMEOUT",
    "COMMAND_TIMEOUT",
    "GIT_LOG_TIMEOUT",
    "INSTALL_TIMEOUT",
    "BaseDeployer",
    "PackageManager",
]
