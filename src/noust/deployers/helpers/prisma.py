# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Prisma ORM helper for deployers.

Handles detection and setup of Prisma in Node.js applications.
"""

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import DeploymentError
from noust.core.logger import Logger
from noust.deployers.helpers.hooks import prisma_applied_migrations


@dataclass(frozen=True)
class PrismaMigration:
    """
    What ``prisma migrate`` did.

    Attributes:
        command: What ran, as one line.
        output: Its standard output and error, as it printed them.
        applied: Whether it applied at least one migration, read from its
            own words ("Applying migration" against "No pending migrations").
    """

    command: str
    output: str
    applied: bool


class PrismaHelper:
    """
    Helper for Prisma ORM operations.

    Provides detection and setup functionality for applications
    using Prisma as their ORM.
    """

    def __init__(
        self,
        logger: Logger | None = None,
        run_command: Callable | None = None,
        get_exec_command: Callable | None = None,
    ):
        """
        Initialize Prisma helper.

        Args:
            logger: Logger instance for output.
            run_command: Function to run commands.
            get_exec_command: Function to get package manager exec command.
        """
        self.logger = logger or Logger()
        self._run_command = run_command
        self._get_exec_command = get_exec_command

    def detect(self, app_path: Path) -> bool:
        """
        Detect if project uses Prisma ORM.

        Args:
            app_path: Path to the application directory.

        Returns:
            True if Prisma is detected.
        """
        if not app_path or not app_path.exists():
            return False

        # Check for prisma directory
        if (app_path / "prisma").exists():
            return True

        # Check package.json for prisma
        package_json = app_path / "package.json"
        if package_json.exists():
            try:
                with open(package_json) as f:
                    pkg = json.load(f)
                    deps = pkg.get("dependencies", {})
                    dev_deps = pkg.get("devDependencies", {})
                    if "@prisma/client" in deps or "prisma" in dev_deps:
                        return True
            except (json.JSONDecodeError, OSError) as e:
                self.logger.debug(f"Failed to read package.json for Prisma detection: {e}")

        return False

    def generate(self, app_path: Path) -> bool:
        """
        Generate Prisma client.

        Args:
            app_path: Path to the application directory.

        Returns:
            True if successful or not needed.
        """
        if not self._run_command or not self._get_exec_command:
            self.logger.warning("Prisma generate skipped: no command runner configured")
            return True

        self.logger.substep("Generating Prisma client")

        command = self._get_exec_command("prisma generate")
        result = self._run_command(command, cwd=app_path, timeout=120)

        if not result.success:
            self.logger.warning(f"Prisma generate failed: {result.stderr}")
            # Don't fail the whole deployment for this
            return True

        return True

    def migrate(self, app_path: Path, deploy: bool = True) -> PrismaMigration | None:
        """
        Run Prisma migrations.

        A migration that fails aborts the deployment (3.2; before, it was a
        warning): new code serving against the old schema is the failure the
        deployment exists to prevent.

        Args:
            app_path: Path to the application directory.
            deploy: If True, run deploy (production), else run dev.

        Returns:
            What ran and whether it applied anything; None when no runner is
            configured and nothing ran.

        Raises:
            DeploymentError: The migration failed, with Prisma's own output.
        """
        if not self._run_command or not self._get_exec_command:
            self.logger.warning("Prisma migrate skipped: no command runner configured")
            return None

        if deploy:
            self.logger.substep("Running Prisma migrations (deploy)")
            command = self._get_exec_command("prisma migrate deploy")
        else:
            self.logger.substep("Running Prisma migrations (dev)")
            command = self._get_exec_command("prisma migrate dev")

        result = self._run_command(command, cwd=app_path, timeout=300)
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        line = shlex.join(command)

        if not result.success:
            raise DeploymentError(
                f"Prisma migrations failed ({line} exited with {result.exit_code})",
                details="Nothing was switched over: what served before is still serving. Fix "
                "the migration and deploy again, or declare the migration as a pre_deploy hook "
                "in noust.yaml to run it your own way." + (f"\n\n{output}" if output else ""),
                output=output,
            )

        return PrismaMigration(
            command=line, output=output, applied=prisma_applied_migrations(output)
        )
