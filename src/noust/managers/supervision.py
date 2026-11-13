# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What keeps a deployment alive: systemd on a server, Docker on a container host.

A server keeps a stack or a container application alive with a unit, and every deployer, the health
gate and the lifecycle reach it through :class:`~noust.managers.service_manager.ServiceManager`. The
container of a NAS has no systemd and keeps what it deploys alive with Docker's own restart policy, so
the same seam has a second engine, :class:`~noust.deployers.compose_supervisor.ComposeSupervisor`, and
this module is the one place that says which of the two a caller gets (rule 3). A caller does not ask
"am I in a container": it asks :func:`service_control`.

The caller passes in the class it would have built, and gets an instance of it back on a server. That
is deliberate and not a convenience: modules look ``ServiceManager`` up in their own namespace, and the
tests of every one of them replace it there, so the name has to be the module's, resolved when the
caller asks. A server is exactly what it was.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from noust import central

__all__ = ["keeps_alive_with_docker", "remember_stack", "service_control"]

T = TypeVar("T")


def keeps_alive_with_docker() -> bool:
    """
    Say whether Docker, and not systemd, keeps what is deployed here alive.

    Returns:
        True on a container host: a hub that was told to deploy containers on its machine's Docker. A
        server, and a hub that was not told, answer False.

    Raises:
        ConfigError: When ``central.role`` or ``central.deploy_here`` holds what is not one of their
            values.
    """
    return central.is_container_host()


def service_control(systemd: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Build what starts, stops, restarts and reads the state and the log of a deployment.

    Args:
        systemd: What a server uses, called with ``args`` and ``kwargs`` (``ServiceManager``, as the
            caller's module holds it).
        *args: What it is built with.
        **kwargs: The same, by name. ``verbose`` and ``runner`` are what a container host's engine
            reads of them, and ``stack`` (the deployer of a stack that is being deployed and is not
            in the store yet) is for it alone and is never passed on to ``systemd``.

    Returns:
        ``systemd(*args, **kwargs)`` on a server. On a container host, the engine that does the same
        with ``docker compose``, which answers what a unit's manager answers for a stack or a
        container application and refuses, in a sentence, what only systemd can do. It is typed as
        what the caller asked for, because it is used as that: the refusal is what keeps it honest.
    """
    # The deployer of a stack that is being deployed for the first time: only Docker's engine needs it, so a
    # server's manager never sees the argument.
    stack = kwargs.pop("stack", None)
    if not keeps_alive_with_docker():
        return systemd(*args, **kwargs)
    from noust.deployers.compose_supervisor import ComposeSupervisor

    return cast(
        T,
        ComposeSupervisor(
            verbose=bool(kwargs.get("verbose", False)), runner=kwargs.get("runner"), stack=stack
        ),
    )


def remember_stack(deployer: Any) -> None:
    """
    Record what a unit would have recorded of a stack that was just registered.

    A server's unit names the compose file and the profiles a stack runs with, and an update reads
    them back from it. Where Docker keeps the stack alive there is no unit, so they are kept in the
    one place the operator's own choice of them lives (``app_compose``). Nothing is written on a
    server.

    Args:
        deployer: The Compose deployer of the stack, whose application is registered.
    """
    if keeps_alive_with_docker():
        from noust.deployers.compose_supervisor import remember_options

        remember_options(deployer)
