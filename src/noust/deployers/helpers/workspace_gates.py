# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The health gate of an application that runs as several units: a monorepo's workspaces.

A single unit passes :class:`~noust.deployers.helpers.health_gate.HealthGate`. A monorepo runs one
unit per workspace, and the question after a restart is "does every one of them answer", not "does
one". The rule is the monorepo's own since 3.2: every unit is restarted before any is probed, so
the stretch in which some workspaces run the new build and others the old one is as short as it
can be; a unit with a port is probed over HTTP with the application's health check, and one with
none (a worker) has to be running.

This module is the one implementation of that rule. The monorepo deployer asks it after an
in-place rebuild and after switching a release; the activation of a release, a rollback and the
migration onto releases (:func:`noust.deployers.lifecycle.health_gate_for`) ask it through the same
``restart_and_probe()`` a single unit's gate answers, so none of them needs to know there are
several.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.store import Service
from noust.deployers.helpers.health import wait_until_healthy
from noust.deployers.helpers.health_gate import HealthCheck, HealthGate, Probe
from noust.managers.service_manager import ServiceManager

__all__ = ["SETTLE_SECONDS", "WorkspaceGates", "WorkspaceRestart"]

#: systemd reports a unit active the instant it forks, so a unit with no port to probe gets this
#: long to fail before its state is believed.
SETTLE_SECONDS = 2


@dataclass(frozen=True)
class WorkspaceRestart:
    """
    What restarting every workspace's unit and probing it found.

    Attributes:
        restarted: The units restarted.
        healthy: Whether every one of them passed its health gate.
        evidence: For each one that did not, the probes and its journal.
    """

    restarted: tuple[str, ...]
    healthy: bool
    evidence: str


class WorkspaceGates:
    """
    Restart every unit of an application and probe each one.

    Args:
        units: The units, in the order they are restarted.
        check: The application's health check: path, statuses and wait.
        services: The service manager that restarts and reads the units.
        logger: Where the restarts and the probes are reported.
        probe: The HTTP probe; looked up by the caller at call time so a test that replaces the
            probe of its own module is heard.
        rehearsing: Under ``--dry-run`` nothing was restarted, so there is nothing to probe.
        settle: Seconds a unit with no port gets before its state is believed.
    """

    def __init__(
        self,
        units: Sequence[Service],
        *,
        check: HealthCheck,
        services: ServiceManager,
        logger: Logger,
        probe: Probe = wait_until_healthy,
        rehearsing: bool = False,
        settle: float = SETTLE_SECONDS,
    ) -> None:
        self.units = list(units)
        self.check = check
        self.services = services
        self.logger = logger
        self.probe = probe
        self.rehearsing = rehearsing
        self.settle = settle

    def gate(self, unit: Service) -> HealthGate:
        """
        Build the gate one workspace's unit must pass.

        Args:
            unit: The unit.

        Returns:
            The gate, probing the unit's own port. It never restarts: every unit is restarted
            before any is probed.
        """
        return HealthGate(
            unit=unit.name,
            url=self.check.url(unit.port) if unit.port else None,
            check=self.check,
            services=self.services,
            logger=self.logger,
            probe=self.probe,
            restart=lambda: None,
        )

    def outcome(self) -> WorkspaceRestart:
        """
        Restart every unit, then probe each.

        Returns:
            What was restarted, and whether every unit passed.
        """
        restarted: list[str] = []
        failures: list[str] = []
        for unit in self.units:
            self.logger.substep(f"Restarting {unit.name}")
            try:
                self.services.restart(unit.name)
            except NoustError as exc:
                failures.append(self.gate(unit).evidence(str(exc)))
            else:
                restarted.append(unit.name)

        if not self.rehearsing:
            if any(not unit.port for unit in self.units):
                time.sleep(self.settle)
            for unit in self.units:
                if unit.name not in restarted:
                    continue
                gate = self.gate(unit)
                if unit.port:
                    healthy, evidence = gate.restart_and_probe()
                    if not healthy:
                        failures.append(f"{unit.name}: {evidence}")
                elif not self.services.get_status(unit.name).get("active"):
                    failures.append(gate.evidence(f"{unit.name} is not running."))

        return WorkspaceRestart(
            restarted=tuple(restarted), healthy=not failures, evidence="\n\n".join(failures)
        )

    def restart_and_probe(self) -> tuple[bool, str]:
        """
        Restart every unit and ask whether the application answers.

        The shape a single unit's gate answers, so a caller that restarts "the application"
        does not care how many units it has.

        Returns:
            Whether every unit is healthy, and when not, the evidence of each that was not.
        """
        outcome = self.outcome()
        return outcome.healthy, outcome.evidence
