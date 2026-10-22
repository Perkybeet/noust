# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Remix and React Router deployer for Noust.

Remix, and React Router v7 in framework mode (the same project after the two
merged), build a server and a client bundle: ``build/server/index.js`` run by
``remix-serve`` or ``react-router-serve``, which read ``PORT`` and ``HOST``.
A project with its own server (Express, Hono) says so with a ``start`` script,
which is what runs; one in SPA mode (``ssr: false``) builds only
``build/client``, served as files.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from noust.core.fs import is_rehearsal
from noust.deployers.node_framework import (
    LOOPBACK,
    FrameworkPlan,
    NodeFrameworkDeployer,
    config_text,
    dependencies_of,
    script_of,
    without_comments,
)
from noust.deployers.registry import DeployerRegistry

_ROUTER_CONFIG = ("react-router.config.ts", "react-router.config.js", "react-router.config.mjs")
_VITE_CONFIG = ("vite.config.ts", "vite.config.js", "vite.config.mjs")
_SPA = re.compile(r"""\bssr\s*:\s*false\b""")

_REMIX_PACKAGES = (
    "@remix-run/react",
    "@remix-run/node",
    "@remix-run/serve",
    "@remix-run/dev",
    "@remix-run/express",
)
_ROUTER_PACKAGES = ("@react-router/dev", "@react-router/node", "@react-router/serve")


class RemixDeployer(NodeFrameworkDeployer):
    """Deployer for Remix and React Router (framework mode) applications."""

    APP_TYPE = "remix"
    DISPLAY_NAME = "Remix / React Router"

    DETECTION_FILES: ClassVar[list[str]] = list(_ROUTER_CONFIG)
    MARKERS: ClassVar[tuple[str, ...]] = (*_REMIX_PACKAGES, *_ROUTER_PACKAGES)
    CONFIG_FILES: ClassVar[tuple[str, ...]] = _ROUTER_CONFIG

    def plan_for(self, project: Path, package: Mapping[str, object]) -> FrameworkPlan:
        """
        Say what a Remix or React Router project builds into.

        Args:
            project: The project directory.
            package: Its ``package.json``.

        Returns:
            The project's ``start`` script, or the framework's own server over
            its build; the client bundle alone for an SPA.
        """
        dependencies = dependencies_of(package)
        router = any(name in dependencies for name in _ROUTER_PACKAGES) or any(
            (project / name).is_file() for name in _ROUTER_CONFIG
        )
        config = without_comments(config_text(project, (*_ROUTER_CONFIG, *_VITE_CONFIG)))
        if _SPA.search(config):
            return FrameworkPlan(serves=False, publish="build/client")
        environment = {"HOST": LOOPBACK}
        if script_of(package, "start") is not None:
            return FrameworkPlan(
                serves=True, start=self._run_script("start"), environment=environment
            )
        serve = "react-router-serve" if router else "remix-serve"
        package_name = "@react-router/serve" if router else "@remix-run/serve"
        if package_name not in dependencies:
            return FrameworkPlan(
                serves=False,
                problem=(
                    f"This project has no start script and does not depend on {package_name}, "
                    "so there is nothing to run its build.\nAdd "
                    f'"start": "{serve} ./build/server/index.js" to package.json, or say what '
                    "to run: noust app build set DOMAIN --start 'node server.js'."
                ),
            )
        # The Vite build writes build/server/index.js, the classic compiler
        # build/index.js; before a build there is neither, and Vite is current.
        entry = "./build/server/index.js"
        legacy = project / "build" / "index.js"
        if not is_rehearsal() and legacy.is_file() and not (project / "build" / "server").is_dir():
            entry = "./build/index.js"
        return FrameworkPlan(
            serves=True, start=f"{self._tool(serve)} {entry}", environment=environment
        )


DeployerRegistry.register(RemixDeployer)
