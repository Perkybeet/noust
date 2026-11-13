# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Keeping a database instance alive with Docker, where a server keeps it alive with a unit.

On a server an instance (a database engine Noust runs in a container, ``noust db instance``) is a unit of
systemd that runs its Compose project: the unit starts it, stops it (``compose down``, which removes the
container and keeps the volumes) and brings it back at boot. The central of a NAS has no systemd, and an
engine in a container is one of the things it is offered. What brings it back there is the restart policy
its Compose file already carries (``unless-stopped``), and what the unit did besides is a few
``docker compose`` commands, which this class runs against the same file and the same project, so that
:class:`~noust.managers.database.managed.ManagedInstances` is one implementation with two engines behind
the one seam it already has (``units()``), and not a second manager.

It answers what that manager asks of a unit and nothing else: a call it does not know is an
``AttributeError`` at the first test, never a silent no-op.
"""

from __future__ import annotations

from types import SimpleNamespace

from noust.core import paths
from noust.core.docker.project import compose_argv
from noust.core.exceptions import ServiceError
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.managers.database import instance_layout as layout

__all__ = ["InstanceSupervisor"]

#: Seconds ``docker compose`` may take: the first start pulls an image.
COMPOSE_TIMEOUT = 600


class InstanceSupervisor:
    """
    The service manager of a machine without systemd, for the database instances in Docker.

    Args:
        runner: Runs Docker; the process-wide one by default.
    """

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner

    @property
    def runner(self) -> CommandRunner:
        """The runner Docker is asked through."""
        return self._runner or get_runner()

    @staticmethod
    def _instance(unit: str) -> str:
        """
        Args:
            unit: The name a unit would have (``noust-db-pg16``).

        Returns:
            The instance's name.
        """
        return unit.removeprefix(paths.DB_INSTANCE_UNIT_PREFIX)

    def _compose(self, unit: str, *args: str) -> CommandResult:
        """
        Run one ``docker compose`` command against the instance's project.

        Args:
            unit: The instance's unit name.
            *args: The subcommand and its arguments.

        Returns:
            The outcome.
        """
        name = self._instance(unit)
        return self.runner.run(
            compose_argv(layout.project_name(name), layout.compose_path(name), *args),
            cwd=layout.directory(name),
            timeout=COMPOSE_TIMEOUT,
        )

    def inspect_unit(self, unit: str, *, serving: bool = True) -> SimpleNamespace:
        """
        Say whether the instance has anything here: its Compose file is where a unit's file would be.

        Args:
            unit: The instance's unit name.
            serving: Ignored.

        Returns:
            ``exists``, ``unit`` and ``path``, the fields a unit's inspection has that the manager reads.
        """
        compose = layout.compose_path(self._instance(unit))
        return SimpleNamespace(exists=compose.exists(), unit=unit, path=compose)

    def install_unit(self, *args: object, **kwargs: object) -> None:
        """Write nothing: there is no unit, and the restart policy is in the Compose file."""

    def enable(self, unit: str) -> bool:
        """
        Returns:
            True: what starts it at boot is the restart policy of its container.
        """
        return True

    def start(self, unit: str) -> None:
        """
        Bring the instance's project up.

        Args:
            unit: The instance's unit name.

        Raises:
            ServiceError: Compose refused, with its own words.
        """
        started = self._compose(unit, "up", "-d", "--remove-orphans")
        if not started.success:
            raise ServiceError(
                f"Failed to start {unit}", details=(started.stderr or started.stdout).strip()
            )

    def restart(self, unit: str) -> None:
        """
        Recreate the instance's container, so that its current files and settings are picked up.

        Args:
            unit: The instance's unit name.

        Raises:
            ServiceError: Compose refused, with its own words.
        """
        done = self._compose(unit, "up", "-d", "--force-recreate", "--remove-orphans")
        if not done.success:
            raise ServiceError(
                f"Failed to restart {unit}", details=(done.stderr or done.stdout).strip()
            )

    def stop(self, unit: str) -> bool:
        """
        Take the instance's project down, as the unit's stop does: the container goes, the volumes stay.

        Args:
            unit: The instance's unit name.

        Returns:
            True when it is down afterwards.
        """
        return self._compose(unit, "down").success

    def delete_service(self, unit: str, *, keep_record: bool = False) -> None:
        """
        Take the project down, as removing its unit stopped it.

        Args:
            unit: The instance's unit name.
            keep_record: Ignored: there is no record of a unit to keep.
        """
        self._compose(unit, "down")
