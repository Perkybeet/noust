# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose stack that runs outside its unit, and handing it back (item 73).

On a production server a stack's unit was stopped and disabled while its
containers had been up for days, because someone ran ``docker compose up -d``
by hand. Everything that judged the application by its unit called it
stopped - ``noust list``, the console, the overview's count - while the site
served. It is neither stopped nor Noust's: a reboot would not bring it back
(the unit is not enabled to) and nothing Noust does to the unit applies to
what runs. That is its own state, ``Running outside Noust``.

This module is the one answer to two questions:

- **Which containers are an application's.** :func:`stack_identity` names
  what identifies them: the project the store pins (an adopted stack's), the
  directory Compose recorded as the project's, and the name Compose derives
  from that directory. The monitor's sampling plan builds its ``docker ps``
  filters from the same identity.
- **Which stacks run outside their units.** :func:`running_outside_units`
  asks Docker once, through the one container lister
  (:func:`~noust.deployers.compose_adopt.list_stack_containers`), for every
  stack it is given: a caller passes only the stacks whose units are not
  active, so a server whose stacks all run under their units never asks.

Handing a stack back (``noust app reclaim``, ``POST /api/apps/{d}/reclaim``)
enables its unit and starts it. Starting it runs ``docker compose up -d``,
which leaves containers that already run with the same configuration as they
are; whether it would is proven first with the rehearsal adoption uses
(:func:`~noust.deployers.compose_adopt.rehearse_up`), and a rehearsal that
would recreate something is refused with Compose's own output unless the
operator accepts it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from noust.core import audit
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError
from noust.core.fs import is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, AppType, NoustStore, get_store
from noust.deployers.compose_adopt import (
    PROJECT_LABEL,
    WORKING_DIR_LABEL,
    StackContainer,
    guard_running_stack,
    list_stack_containers,
    rehearse_up,
)
from noust.managers.service_manager import ServiceManager
from noust.validators.domain import validate_domain


@dataclass(frozen=True)
class StackIdentity:
    """
    What tells an application's containers apart from every other stack's.

    Attributes:
        pinned: The project the store pins for it (``apps.compose_project``),
            an adopted stack's; None for one Compose names.
        working_dir: The application's directory, which Compose records as
            the project directory of a stack started from it.
        derived: The project Compose derives from that directory's name.
    """

    pinned: str | None
    working_dir: str
    derived: str | None

    def filters(self) -> list[str]:
        """
        Build the ``docker ps --filter`` values that select the stack, most precise first.

        Returns:
            The pinned project's label, the working directory's, then the
            derived project's when it differs from the pinned one.
        """
        filters: list[str] = []
        if self.pinned:
            filters.append(f"label={PROJECT_LABEL}={self.pinned}")
        filters.append(f"label={WORKING_DIR_LABEL}={self.working_dir}")
        if self.derived and self.derived != self.pinned:
            filters.append(f"label={PROJECT_LABEL}={self.derived}")
        return filters

    def matches(self, container: StackContainer) -> bool:
        """
        Tell whether a container is the stack's, by the same labels :meth:`filters` selects.

        Args:
            container: A container ``docker ps`` listed.

        Returns:
            True when its project is the pinned or the derived one, or its
            recorded project directory is the application's.
        """
        if container.project and container.project in (self.pinned, self.derived):
            return True
        recorded = container.working_dir
        return bool(recorded) and os.path.normpath(recorded) == os.path.normpath(self.working_dir)


def stack_identity(app: Any) -> StackIdentity:
    """
    Name what identifies an application's containers.

    Args:
        app: The application's row.

    Returns:
        Its identity; reading it touches no file and runs nothing.
    """
    from noust.deployers.docker_compose import compose_project_name
    from noust.deployers.helpers.layout import app_root

    app_path = app_root(app)
    pinned = getattr(app, "compose_project", None) or None
    derived = compose_project_name(app_path, None) if app_path.name else None
    return StackIdentity(pinned=pinned, working_dir=str(app_path), derived=derived)


def running_outside_units(
    apps: Sequence[App], *, runner: CommandRunner | None = None
) -> dict[str, tuple[str, ...]]:
    """
    Find which of these stacks have containers running, with one ``docker ps``.

    The caller passes the stacks whose units are not active: for those, a
    running container is a stack that runs outside its unit. Any application
    that is not a Compose stack is ignored, and with none nothing is asked.

    Args:
        apps: The applications to look at.
        runner: The runner docker is asked through.

    Returns:
        Domain to the names of its running containers, sorted, for each stack
        that has any.

    Raises:
        DeploymentError: Docker cannot be asked.
    """
    stacks = [app for app in apps if app.app_type == AppType.DOCKER_COMPOSE.value]
    if not stacks:
        return {}
    runner = runner if runner is not None else get_runner()
    running = [c for c in list_stack_containers(runner=runner) if c.state == "running"]
    found: dict[str, tuple[str, ...]] = {}
    for app in stacks:
        identity = stack_identity(app)
        names = tuple(sorted(c.name for c in running if identity.matches(c)))
        if names:
            found[app.domain] = names
    return found


# -- Handing it back -----------------------------------------------------------------


@dataclass(frozen=True)
class ReclaimPlan:
    """
    What handing a stack back to Noust would do, found without changing anything.

    Attributes:
        domain: The application's domain.
        unit: Its unit, enabled and started by the hand-back.
        project: The Compose project its commands address, or None when the
            stack names its own.
        containers: The names of its containers that run now.
        enabled: Whether the unit was already enabled to start at boot.
        dry_run: Compose's output for the rehearsed ``up``, verbatim.
        changes: The lines of it that would create, recreate or remove
            something (accepted, or the plan would not exist).
    """

    domain: str
    unit: str
    project: str | None
    containers: tuple[str, ...]
    enabled: bool
    dry_run: str
    changes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as JSON-ready values.
        """
        return {
            "domain": self.domain,
            "unit": self.unit,
            "project": self.project,
            "containers": list(self.containers),
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "changes": list(self.changes),
        }


def plan_reclaim(
    domain: str,
    *,
    accept_recreate: bool = False,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
) -> ReclaimPlan:
    """
    Check that a stack runs outside its unit and that starting the unit would keep it as it is.

    Args:
        domain: The application's domain.
        accept_recreate: Hand it back even when the rehearsed ``up`` would
            change something.
        runner: The runner docker and systemd are asked through.
        store: The store the application is read from.

    Returns:
        The plan.

    Raises:
        DeploymentError: The application is not deployed, is not a Compose
            stack, its unit is missing or already running, or none of its
            containers runs.
        AdoptionRefusedError: The rehearsed ``up`` failed or would change
            something, and that was not accepted.
        DeploymentError: Also when the compose file asks for root
            (``privileged``, the Docker socket) the running stack is not
            proven to have, without an exception.
        ServiceError: The unit is not Noust's.
    """
    from noust.deployers.docker_compose import compose_project_name, stack_deployer

    domain = validate_domain(domain)
    runner = runner if runner is not None else get_runner()
    store = store if store is not None else get_store()
    app = store.get_app(domain)
    if app is None:
        raise DeploymentError(f"{domain} is not deployed", details="See what is with: noust list")
    if app.app_type != AppType.DOCKER_COMPOSE.value:
        raise DeploymentError(
            f"{domain} is not a Docker Compose stack",
            details=f"Only a stack can run outside its unit. Start it with: noust start {domain}",
        )

    deployer = stack_deployer(app, runner=runner)
    services = ServiceManager(verbose=False, runner=runner)
    unit = deployer.app_name
    status = services.get_status(unit)
    if not status.get("exists"):
        raise DeploymentError(
            f"{domain} has no unit {unit}.service to hand it back to",
            details=f"Write it again with: noust update {domain}",
        )
    if status.get("active"):
        raise DeploymentError(
            f"{domain} already runs under its unit {unit}.service",
            details=f"Nothing to hand back. See it with: noust status {domain}",
        )
    containers = running_outside_units([app], runner=runner).get(domain, ())
    if not containers:
        raise DeploymentError(
            f"None of {domain}'s containers is running",
            details=f"It is stopped, not running outside Noust. Start it with: noust start {domain}",
        )

    deployer._discover_compose_file()
    # None: the project the unit's own commands address, as _compose builds it.
    dry_run, changes = rehearse_up(
        deployer,
        None,
        accept_recreate=accept_recreate,
        action="Handing the stack back",
        retry="hand it back",
    )
    # Starting the unit runs ``up -d`` on the file as it is now, which may
    # have been edited since the stack last ran under Noust.
    guard_running_stack(deployer, running=True, dry_run=dry_run, changes=changes)
    project = deployer._pinned_project() or compose_project_name(
        deployer.app_path, deployer.compose_path
    )
    return ReclaimPlan(
        domain=domain,
        unit=unit,
        project=project,
        containers=containers,
        enabled=bool(status.get("enabled")),
        dry_run=dry_run,
        changes=changes,
    )


@dataclass(frozen=True)
class ReclaimResult:
    """
    What a hand-back did.

    Attributes:
        plan: What was handed back.
        reclaimed: False for a rehearsal, which changes nothing.
    """

    plan: ReclaimPlan
    reclaimed: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan's values, with what was done.
        """
        return {**self.plan.to_dict(), "reclaimed": self.reclaimed}


def reclaim(
    plan: ReclaimPlan,
    *,
    runner: CommandRunner | None = None,
) -> ReclaimResult:
    """
    Enable the stack's unit and start it, as a plan found it safe to.

    Args:
        plan: What :func:`plan_reclaim` found.
        runner: The runner systemd goes through.

    Returns:
        What was done; nothing under ``--dry-run``.

    Raises:
        DeploymentError: The unit started since the plan was made.
        ServiceError: systemd refused to enable or start the unit, or the
            unit is not Noust's.
        AppBusyError: Another operation is running on the application.
    """
    if is_rehearsal():
        return ReclaimResult(plan=plan, reclaimed=False)
    runner = runner if runner is not None else get_runner()
    services = ServiceManager(verbose=False, runner=runner)
    with app_lock(plan.domain, "reclaim"):
        # Checked again under the lock: the plan was made without it, and an
        # update or a start in between would make this a second start of a
        # unit that already runs what it was proven against.
        if services.get_status(plan.unit).get("active"):
            raise DeploymentError(
                f"{plan.domain} already runs under its unit {plan.unit}.service",
                details=f"Something started it since the check. See it with: noust status "
                f"{plan.domain}",
            )
        # Enabled first: a start that fails still leaves the stack coming
        # back at the next boot under the unit, which is what was asked.
        services.enable(plan.unit)
        services.start(plan.unit)
    audit.record(
        "apps.reclaim",
        target=f"app:{plan.domain}",
        details={
            "unit": plan.unit,
            "project": plan.project,
            "containers": list(plan.containers),
            "accepted_changes": list(plan.changes),
        },
    )
    return ReclaimResult(plan=plan, reclaimed=True)


def reclaim_stack(
    domain: str,
    *,
    accept_recreate: bool = False,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
) -> ReclaimResult:
    """
    Plan a hand-back and carry it out.

    Args:
        domain: The application's domain.
        accept_recreate: Hand it back even when the rehearsed ``up`` would
            change something.
        runner: The runner docker and systemd go through.
        store: The store.

    Returns:
        What was done.

    Raises:
        DeploymentError: See :func:`plan_reclaim`.
        AdoptionRefusedError: See :func:`plan_reclaim`.
        ServiceError: See :func:`reclaim`.
    """
    plan = plan_reclaim(domain, accept_recreate=accept_recreate, runner=runner, store=store)
    return reclaim(plan, runner=runner)


__all__ = [
    "ReclaimPlan",
    "ReclaimResult",
    "StackIdentity",
    "plan_reclaim",
    "reclaim",
    "reclaim_stack",
    "running_outside_units",
    "stack_identity",
]
