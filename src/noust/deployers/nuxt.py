# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Nuxt deployer for Noust.

Nuxt 3 and 4 build with Nitro, whose ``node-server`` preset leaves a
self-contained server in ``.output/server/index.mjs`` that reads ``PORT`` and
``HOST``; ``nuxt generate`` (or the ``static`` preset) leaves files in
``.output/public`` instead. Nuxt 2 runs ``nuxt start`` from the project and
generates into ``dist``. Which one a project is, is read from its ``build``
script and its ``nuxt.config``.
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
    major_of,
    script_of,
    without_comments,
)
from noust.deployers.registry import DeployerRegistry

#: The package Nuxt 3 and later is published as, and the older names.
_PACKAGES = ("nuxt", "nuxt3", "nuxt-edge", "nuxt-nightly")

_STATIC_PRESET = re.compile(r"preset\s*:\s*['\"]static['\"]")
_STATIC_TARGET = re.compile(r"target\s*:\s*['\"]static['\"]")


class NuxtDeployer(NodeFrameworkDeployer):
    """
    Deployer for Nuxt applications, server-rendered or generated.

    Server-rendered Nuxt runs Nitro's server behind the proxy; a generated
    site is the static files the web server serves.
    """

    APP_TYPE = "nuxt"
    DISPLAY_NAME = "Nuxt"

    DETECTION_FILES: ClassVar[list[str]] = ["nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs"]
    MARKERS: ClassVar[tuple[str, ...]] = _PACKAGES
    CONFIG_FILES: ClassVar[tuple[str, ...]] = (
        "nuxt.config.ts",
        "nuxt.config.js",
        "nuxt.config.mjs",
    )

    def plan_for(self, project: Path, package: Mapping[str, object]) -> FrameworkPlan:
        """
        Say what a Nuxt project builds into.

        Args:
            project: The project directory.
            package: Its ``package.json``.

        Returns:
            Nitro's server or the generated files; for Nuxt 2, its own server
            or ``dist``.
        """
        dependencies = dependencies_of(package)
        spec = next((dependencies[name] for name in _PACKAGES if name in dependencies), "")
        # A spec with no number (latest, a git URL) is the current Nuxt.
        major = major_of(spec) or 3
        build = script_of(package, "build") or ""
        config = without_comments(config_text(project, self.CONFIG_FILES))
        generated = (
            "generate" in build
            or bool(_STATIC_PRESET.search(config))
            or (major < 3 and bool(_STATIC_TARGET.search(config)))
        )
        if major >= 3:
            if generated:
                return FrameworkPlan(serves=False, publish=".output/public")
            return FrameworkPlan(
                serves=True,
                start="node .output/server/index.mjs",
                environment={"HOST": LOOPBACK},
            )
        if generated:
            return FrameworkPlan(serves=False, publish="dist")
        return FrameworkPlan(
            serves=True, start=self._run_script("start"), environment={"HOST": LOOPBACK}
        )

    def get_build_command(self) -> list[str]:
        """
        Get the build command.

        Returns:
            ``<pm> run build``; for a Nuxt 2 static target that has a
            ``generate`` script and no generating ``build``, that script.
        """
        from noust.deployers.node_framework import read_package

        package = read_package(self.build_path)
        plan = self.plan()
        if not plan.serves and "generate" not in (script_of(package, "build") or ""):
            if script_of(package, "generate") is not None:
                return self._get_pm_run_command("generate")
        return super().get_build_command()


DeployerRegistry.register(NuxtDeployer)
