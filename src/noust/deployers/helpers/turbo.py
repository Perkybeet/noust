# Copyright (c) 2024-2025 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turborepo helper for monorepo deployers.

Handles parsing of turbo.json configuration and provides build
pipeline information for monorepo deployments.
"""

import json
from pathlib import Path

from noust.core.logger import Logger


class TurboHelper:
    """
    Helper for Turborepo configuration analysis.

    Provides parsing of turbo.json, build pipeline detection,
    and dependency order calculation.
    """

    def __init__(self, logger: Logger | None = None):
        """
        Initialize Turborepo helper.

        Args:
            logger: Logger instance for output.
        """
        self.logger = logger or Logger()
        self._config: dict | None = None
        self._config_path: Path | None = None

    def detect(self, app_path: Path) -> bool:
        """
        Detect if the project uses Turborepo.

        Args:
            app_path: Root path of the project.

        Returns:
            True if Turborepo is detected.
        """
        turbo_json = app_path / "turbo.json"
        if turbo_json.exists():
            return True

        # Also check package.json for turbo field
        package_json = app_path / "package.json"
        if package_json.exists():
            try:
                with open(package_json) as f:
                    pkg = json.load(f)
                    return "turbo" in pkg
            except (json.JSONDecodeError, OSError):
                pass

        return False

    def load_config(self, app_path: Path) -> dict:
        """
        Load and parse turbo.json configuration.

        Args:
            app_path: Root path of the project.

        Returns:
            Parsed configuration dictionary.
        """
        if self._config and self._config_path == app_path:
            return self._config

        turbo_json = app_path / "turbo.json"

        if not turbo_json.exists():
            self.logger.debug("No turbo.json found, using defaults")
            self._config = self._default_config()
            self._config_path = app_path
            return self._config

        try:
            with open(turbo_json) as f:
                self._config = json.load(f)
                self._config_path = app_path
                return self._config
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Error reading turbo.json: {e}")
            self._config = self._default_config()
            self._config_path = app_path
            return self._config

    def _default_config(self) -> dict:
        """
        Return default Turborepo configuration.

        Returns:
            Default config dictionary.
        """
        return {
            "tasks": {
                "build": {
                    "dependsOn": ["^build"],
                    "outputs": [".next/**", "dist/**"],
                },
                "dev": {
                    "cache": False,
                    "persistent": True,
                },
                "start": {
                    "dependsOn": ["build"],
                    "cache": False,
                },
            }
        }

    def get_build_command(self, app_path: Path) -> list[str]:
        """
        Get the build command for the monorepo.

        Args:
            app_path: Root path of the project.

        Returns:
            Build command as list of strings.
        """
        config = self.load_config(app_path)

        # Check if build task exists
        tasks = config.get("tasks", config.get("pipeline", {}))
        has_build = "build" in tasks

        if has_build:
            return ["pnpm", "build"]

        # Fallback to turbo run build
        return ["pnpm", "exec", "turbo", "run", "build"]

    def estimate_build_timeout(self, app_path: Path, workspace_count: int) -> int:
        """
        Estimate build timeout based on project size.

        Args:
            app_path: Root path of the project.
            workspace_count: Number of workspaces to build.

        Returns:
            Recommended timeout in seconds.
        """
        # Base timeout: 5 minutes
        base_timeout = 300

        # Add 3 minutes per workspace
        workspace_timeout = workspace_count * 180

        # Cap at 30 minutes
        return min(base_timeout + workspace_timeout, 1800)
