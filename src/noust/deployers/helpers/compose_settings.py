# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Choosing a Compose stack's file, profiles and extra arguments after it exists.

``noust app compose`` and ``/api/apps/{d}/compose`` are clients of this module
(rule 3). A stack is created with a file and profiles
(``noust create --compose-file --compose-profile``) and until now stayed with
what its unit recorded; here they can be changed, and the stack can be given a
few arguments of ``docker compose build`` and ``up`` and the services they
address (:mod:`noust.core.app_compose_store` holds the allowlist).

A change of file or profiles writes the unit again, so a start at boot brings up
what an update brings up; the file is found in the running checkout first, so a
name that does not exist is refused with nothing changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from noust.core import app_compose_store
from noust.core.app_compose_store import ComposeOptions
from noust.core.applock import app_lock
from noust.core.exceptions import NoustError
from noust.core.runner import CommandRunner
from noust.core.store import AppType, NoustStore, get_store
from noust.deployers.docker_compose import stack_deployer


def describe_compose(domain: str, *, store: NoustStore | None = None) -> ComposeOptions:
    """
    Say what a stack is run with.

    Args:
        domain: The stack's domain.
        store: The store; the process-wide one when None.

    Returns:
        The options the operator set; empty ones when none.

    Raises:
        NoustError: The application is unknown or is not a Compose stack.
    """
    _stack(domain, store or get_store())
    return app_compose_store.get_options(domain, store=store)


def _stack(domain: str, store: NoustStore) -> Any:
    """
    Read the row of a stack.

    Args:
        domain: A validated domain.
        store: The store.

    Returns:
        The application's row.

    Raises:
        NoustError: It is unknown, or not a Compose stack.
    """
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    if app.app_type != AppType.DOCKER_COMPOSE.value:
        raise NoustError(
            f"{domain} is not a Docker Compose application",
            details="A compose file, profiles and compose arguments belong to a stack deployed "
            "with --type docker-compose.",
        )
    return app


def change_compose(
    domain: str,
    given: Mapping[str, Any],
    *,
    actor: str | None,
    store: NoustStore | None = None,
    runner: CommandRunner | None = None,
) -> ComposeOptions:
    """
    Change what a stack is run with, keeping what is not named.

    Nothing is written unless all of it is valid; a file that is not in the
    checkout, or a path that leaves it, is refused. A new file or new profiles
    are written to the unit as well.

    Args:
        domain: The stack's domain.
        given: Any of ``compose_file``, ``profiles``, ``build_args``,
            ``up_args`` and ``services``; None values are not changes.
        actor: Who changed it, for the audit trail.
        store: The store; the process-wide one when None.
        runner: What runs ``docker``; the process-wide runner when None.

    Returns:
        The options now in force.

    Raises:
        NoustError: The application is unknown or not a stack.
        ValidationError: A value is not acceptable; ``field`` names it.
        DeploymentError: The compose file is not in the stack's directory.
        AppBusyError: Another operation is running on the stack.
    """
    target = store or get_store()
    app = _stack(domain, target)
    changes = app_compose_store.check_options(
        **{key: value for key, value in given.items() if value is not None}
    )
    with app_lock(domain, "compose settings"):
        before = app_compose_store.get_options(domain, store=target)
        if "compose_file" in changes or "profiles" in changes:
            deployer = stack_deployer(app, runner=runner)
            if "compose_file" in changes:
                deployer.compose_file = changes["compose_file"]
            deployer.compose_profiles = list(changes.get("profiles", before.profiles))
            # Proves the file is in the checkout, and not through a link out of it.
            deployer._discover_compose_file()
            deployer._create_systemd_service()
        return app_compose_store.set_options(domain, changes, actor=actor, store=target)


def reset_compose(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget what the operator chose; the unit's own record applies again.

    Args:
        domain: The stack's domain.
        store: The store; the process-wide one when None.

    Returns:
        Whether there was something to forget.

    Raises:
        NoustError: The application is unknown or not a stack.
    """
    target = store or get_store()
    _stack(domain, target)
    return app_compose_store.reset_options(domain, store=target)
