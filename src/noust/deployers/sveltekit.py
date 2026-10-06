# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SvelteKit deployer for Noust.

SvelteKit builds for the platform its adapter names. ``adapter-node`` leaves a
server in ``build`` (``node build``, reading ``PORT``, ``HOST`` and the
forwarding headers); ``adapter-static`` leaves files; ``adapter-auto`` and the
platform adapters (Vercel, Netlify, Cloudflare) leave nothing a server can run,
so those projects are refused with the way out rather than deployed to a
build that serves nothing.
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

_ADAPTER_IMPORT = re.compile(r"""@sveltejs/adapter-([a-z0-9-]+)""")
_PAGES = re.compile(r"""pages\s*:\s*['"]([^'"]+)['"]""")

_CONFIG_FILES = ("svelte.config.js", "svelte.config.mjs", "svelte.config.ts")

_NOT_DEPLOYABLE = (
    "SvelteKit builds for the platform its adapter names, and {adapter} builds for none this "
    "server is.\nUse @sveltejs/adapter-node to run it as a server (it builds into 'build', "
    "started with: node build) or @sveltejs/adapter-static to serve files, set it in "
    "svelte.config.js, and deploy again. If you build it some other way, say what to run: "
    "noust app build set DOMAIN --start 'node build' (or --publish-directory build)."
)


class SvelteKitDeployer(NodeFrameworkDeployer):
    """Deployer for SvelteKit applications with the Node or the static adapter."""

    APP_TYPE = "sveltekit"
    DISPLAY_NAME = "SvelteKit"

    DETECTION_FILES: ClassVar[list[str]] = list(_CONFIG_FILES)
    MARKERS: ClassVar[tuple[str, ...]] = ("@sveltejs/kit",)
    # svelte.config.js alone is any Svelte project's, SvelteKit or not.
    CONFIG_FILES: ClassVar[tuple[str, ...]] = ()

    def plan_for(self, project: Path, package: Mapping[str, object]) -> FrameworkPlan:
        """
        Say what a SvelteKit project builds into, from its adapter.

        Args:
            project: The project directory.
            package: Its ``package.json``.

        Returns:
            The Node server, the static files, or a plan that says why the
            adapter cannot be deployed.
        """
        dependencies = dependencies_of(package)
        config = without_comments(config_text(project, _CONFIG_FILES))
        imported = _ADAPTER_IMPORT.findall(config)
        if imported:
            adapter = imported[0]
        elif "@sveltejs/adapter-node" in dependencies:
            adapter = "node"
        elif "@sveltejs/adapter-static" in dependencies:
            adapter = "static"
        elif "@sveltejs/adapter-auto" in dependencies or not config:
            adapter = "auto"
        else:
            adapter = "custom"
        if adapter == "node":
            return FrameworkPlan(
                serves=True,
                start="node build",
                environment={
                    "HOST": LOOPBACK,
                    # Behind nginx the origin is what the proxy forwards; without
                    # these a form submission is refused as cross-site.
                    "PROTOCOL_HEADER": "x-forwarded-proto",
                    "HOST_HEADER": "x-forwarded-host",
                    "PORT_HEADER": "x-forwarded-port",
                },
            )
        if adapter == "static":
            pages = _PAGES.search(config)
            return FrameworkPlan(serves=False, publish=pages.group(1) if pages else "build")
        return FrameworkPlan(
            serves=False,
            problem=_NOT_DEPLOYABLE.format(adapter=f"adapter-{adapter}"),
        )


DeployerRegistry.register(SvelteKitDeployer)
