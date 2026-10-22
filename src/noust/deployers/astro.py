# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Astro deployer for Noust.

An Astro site is static files (``dist``) unless it has an adapter. With
``@astrojs/node`` in standalone mode the build leaves a server,
``dist/server/entry.mjs``, that reads ``HOST`` and ``PORT``; the adapters of
other platforms (Vercel, Netlify, Cloudflare, Deno) leave nothing a server of
this kind can run, and the Node adapter's ``middleware`` mode is a module for
somebody else's server. Those are refused with what to do.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from noust.deployers.node_framework import (
    LOOPBACK,
    FrameworkPlan,
    NodeFrameworkDeployer,
    config_text,
    dependencies_of,
    without_comments,
)
from noust.deployers.registry import DeployerRegistry

_CONFIG_FILES = ("astro.config.mjs", "astro.config.ts", "astro.config.js", "astro.config.cjs")
_OUT_DIR = re.compile(r"""outDir\s*:\s*['"](?:\./)?([^'"]+)['"]""")
_MIDDLEWARE = re.compile(r"""mode\s*:\s*['"]middleware['"]""")

#: Adapters for platforms with their own runtime: what they build is not a
#: server this machine runs.
_PLATFORM_ADAPTERS = (
    "@astrojs/vercel",
    "@astrojs/netlify",
    "@astrojs/cloudflare",
    "@astrojs/deno",
)


class AstroDeployer(NodeFrameworkDeployer):
    """Deployer for Astro sites, static or rendered by the Node adapter."""

    APP_TYPE = "astro"
    DISPLAY_NAME = "Astro"

    DETECTION_FILES: ClassVar[list[str]] = list(_CONFIG_FILES)
    MARKERS: ClassVar[tuple[str, ...]] = ("astro",)
    CONFIG_FILES: ClassVar[tuple[str, ...]] = _CONFIG_FILES

    def plan_for(self, project: Path, package: Mapping[str, object]) -> FrameworkPlan:
        """
        Say what an Astro project builds into, from its adapter.

        Args:
            project: The project directory.
            package: Its ``package.json``.

        Returns:
            The standalone server of the Node adapter, the static files, or a
            plan that says why the adapter cannot be deployed.
        """
        dependencies = dependencies_of(package)
        config = without_comments(config_text(project, _CONFIG_FILES))
        out_dir = _OUT_DIR.search(config)
        directory = out_dir.group(1).rstrip("/") if out_dir else "dist"
        if "@astrojs/node" in dependencies:
            if _MIDDLEWARE.search(config):
                return FrameworkPlan(
                    serves=False,
                    problem=(
                        "The Astro Node adapter is in middleware mode, which builds a module "
                        "for your own server, not one to run.\nUse mode: 'standalone' in "
                        "astro.config, or say what to run: noust app build set DOMAIN "
                        "--start 'node your-server.mjs'."
                    ),
                )
            return FrameworkPlan(
                serves=True,
                start=f"node ./{directory}/server/entry.mjs",
                environment={"HOST": LOOPBACK},
            )
        platform = next((name for name in _PLATFORM_ADAPTERS if name in dependencies), None)
        if platform is not None:
            return FrameworkPlan(
                serves=False,
                problem=(
                    f"This Astro site uses {platform}, which builds for that platform, not for "
                    "a server this machine runs.\nUse @astrojs/node (mode: 'standalone') to "
                    "run it as a server, or remove the adapter to serve it as static files."
                ),
            )
        return FrameworkPlan(serves=False, publish=directory)


DeployerRegistry.register(AstroDeployer)
