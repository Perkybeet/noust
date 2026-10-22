# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Angular deployer for Noust.

An Angular application is a client bundle (``dist/<project>/browser``, or
``dist/<project>`` with the older ``browser`` builder) that the web server
serves, unless it renders on the server (``@angular/ssr``, or
``@nguniversal``): then the build also leaves ``dist/<project>/server`` and a
Node server that reads ``PORT``. The project and its output are read from
``angular.json``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from noust.deployers.node_framework import (
    FrameworkPlan,
    NodeFrameworkDeployer,
    dependencies_of,
)
from noust.deployers.registry import DeployerRegistry

#: Dependencies that make an Angular application render on the server.
_SSR_PACKAGES = ("@angular/ssr", "@nguniversal/express-engine", "@angular/platform-server")

#: Builders that put the client bundle in a ``browser`` subdirectory.
_APPLICATION_BUILDERS = (
    ":application",
    ":browser-esbuild",
)

MAX_ANGULAR_JSON = 1024 * 1024


def _read_workspace(project: Path) -> dict:
    """
    Read ``angular.json``.

    Args:
        project: The project directory.

    Returns:
        Its object; empty when it is missing, too large, a link or not JSON.
    """
    path = project / "angular.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ANGULAR_JSON:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _application(workspace: Mapping[str, object]) -> tuple[str, dict]:
    """
    Pick the application a workspace builds for deployment.

    Args:
        workspace: ``angular.json``.

    Returns:
        Its name and its ``build`` target; the default project, else the first
        application. Empty when there is none.
    """
    projects = workspace.get("projects")
    if not isinstance(projects, dict) or not projects:
        return "", {}
    default = workspace.get("defaultProject")
    names = [default] if isinstance(default, str) and default in projects else []
    names += [name for name in projects if name not in names]
    for name in names:
        project = projects[name]
        if (
            not isinstance(project, dict)
            or project.get("projectType", "application") != "application"
        ):
            continue
        architect = project.get("architect") or project.get("targets") or {}
        build = architect.get("build") if isinstance(architect, dict) else None
        if isinstance(build, dict):
            return str(name), build
    return "", {}


class AngularDeployer(NodeFrameworkDeployer):
    """Deployer for Angular applications, with or without server-side rendering."""

    APP_TYPE = "angular"
    DISPLAY_NAME = "Angular"

    DETECTION_FILES: ClassVar[list[str]] = ["angular.json"]
    MARKERS: ClassVar[tuple[str, ...]] = ()
    CONFIG_FILES: ClassVar[tuple[str, ...]] = ()

    def detect(self, path: Path) -> bool:
        """
        Tell whether a tree is an Angular workspace.

        Args:
            path: The fetched source.

        Returns:
            True for an ``angular.json`` next to a package depending on
            ``@angular/core``.
        """
        from noust.deployers.node_framework import read_package

        return (path / "angular.json").is_file() and (
            "@angular/core" in dependencies_of(read_package(path))
        )

    def plan_for(self, project: Path, package: Mapping[str, object]) -> FrameworkPlan:
        """
        Say what an Angular project builds into, from ``angular.json``.

        Args:
            project: The project directory.
            package: Its ``package.json``.

        Returns:
            The Node server of a server-rendered application, or the client
            bundle's directory.
        """
        name, build = _application(_read_workspace(project))
        raw_options = build.get("options")
        options: dict = raw_options if isinstance(raw_options, dict) else {}
        builder = str(build.get("builder", ""))
        output = options.get("outputPath", f"dist/{name}" if name else "dist")
        base = output.get("base") if isinstance(output, dict) else output
        base = str(base or f"dist/{name}").strip("/")
        browser = output.get("browser") if isinstance(output, dict) else None
        has_browser_subdirectory = browser != "" and any(
            builder.endswith(suffix) for suffix in _APPLICATION_BUILDERS
        )
        client = f"{base}/{browser or 'browser'}" if has_browser_subdirectory else base
        dependencies = dependencies_of(package)
        renders = any(name_ in dependencies for name_ in _SSR_PACKAGES) and bool(
            options.get("server") or options.get("ssr") or "server" in _targets(project)
        )
        if not renders:
            return FrameworkPlan(serves=False, publish=client)
        # @angular/ssr's server is server.mjs; Universal's is main.js.
        entry = f"{base}/server/server.mjs"
        if "@nguniversal/express-engine" in dependencies and "@angular/ssr" not in dependencies:
            entry = f"{base}/server/main.js"
        return FrameworkPlan(serves=True, start=f"node {entry}")


def _targets(project: Path) -> set[str]:
    """
    List the build targets of the application, for the older server target.

    Args:
        project: The project directory.

    Returns:
        The names of the architect targets of every project.
    """
    names: set[str] = set()
    projects = _read_workspace(project).get("projects")
    for entry in projects.values() if isinstance(projects, dict) else []:
        architect = (
            entry.get("architect") or entry.get("targets") if isinstance(entry, dict) else None
        )
        if isinstance(architect, dict):
            names.update(str(key) for key in architect)
    return names


DeployerRegistry.register(AngularDeployer)
