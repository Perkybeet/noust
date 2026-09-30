# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write a database's connection settings into an application, behind its gate.

Linking a database, unlinking it and rotating a password all end the same
way: some variables of an application's ``.env`` change and the application
restarts on them. This is that one path. The file is found by
:func:`~noust.deployers.helpers.layout.env_file_for` and written by
:func:`~noust.deployers.helpers.app_env.write_app_env` (never ``app_path``
joined with ``.env``), the new variables are marked secret, and the restart
goes through the same :class:`~noust.deployers.helpers.health_gate.HealthGate`
a deploy passes: an application that does not come up on the new settings
gets its previous environment back, and the error carries the probes and the
journal verbatim.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers.helpers.app_env import read_app_env, write_app_env
from noust.deployers.helpers.layout import app_root
from noust.managers.service_manager import ServiceManager

#: The variables a link writes besides the connection string, when asked:
#: what Laravel, Symfony and hand-written configurations read.
EXTRA_VARIABLES = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")

#: A change to an application's variables: the current ones in, the new out.
EnvEdit = Callable[[dict[str, str]], dict[str, str]]


@dataclass(frozen=True)
class EnvChange:
    """
    What changing an application's environment did.

    Attributes:
        domain: The application.
        changed: Whether any variable changed.
        restarted: Whether the application restarted on it and passed its gate.
        previous: The variables before the change, for a caller that has to
            put them back after a later step fails.
    """

    domain: str
    changed: bool
    restarted: bool
    previous: dict[str, str]


def restart_behind_gate(app: App, store: NoustStore, log: Logger) -> tuple[bool, str]:
    """
    Restart every unit an application runs as, and ask its health gate.

    The same composition as applying resource limits with a restart: every
    unit recorded for the application (a monorepo has one per workspace),
    the gate built from the store's row.

    Args:
        app: The application.
        store: The store.
        log: Where the probes are reported.

    Returns:
        Whether it answered, and the gate's evidence when it did not.
    """
    # Imported here: lifecycle pulls in every deployer, and nothing else in
    # this module needs it.
    from noust.deployers.lifecycle import health_gate_for

    services = ServiceManager()
    units = [s.name for s in store.list_services() if app.id is not None and s.app_id == app.id]
    if not units:
        units = [app_root(app).name]

    def restart_all() -> None:
        for unit in units:
            services.restart(unit)

    gate = health_gate_for(app, store, log, restart=restart_all)
    return gate.restart_and_probe()


def change_app_env(
    app: App,
    edit: EnvEdit,
    *,
    store: NoustStore,
    secret_names: Iterable[str] = (),
    restart: bool = True,
    restore_on_failure: bool = True,
    operation: str = "database settings change",
    log: Logger,
) -> EnvChange:
    """
    Change an application's variables and restart it behind its health gate.

    Args:
        app: The application.
        edit: Turns the current variables into the new ones.
        store: The store, for the secret marks and the gate.
        secret_names: Variables to mark secret, so the console masks them.
        restart: Restart the application on the new variables. A static
            site runs nothing and is never restarted.
        restore_on_failure: When the gate fails, write the previous
            variables back and restart on them before raising. A caller
            that must undo something else first (a password rotation, whose
            old password the engine no longer accepts) passes False and
            restores through :func:`restore_app_env` afterwards.
        operation: What the lock names, for whoever finds it held.
        log: Where progress and the probes are reported.

    Returns:
        What was done.

    Raises:
        DeploymentError: The application did not answer on the new
            variables; with ``restore_on_failure`` the previous ones are back.
        EnvironmentValidationError: A variable cannot be written safely.
        AppBusyError: Another operation holds the application.
    """
    with app_lock(app.domain, operation):
        previous = read_app_env(app)
        updated = edit(dict(previous))
        if updated == previous:
            return EnvChange(domain=app.domain, changed=False, restarted=False, previous=previous)

        write_app_env(app, updated, logger=log)
        marks = dict(app.env_secret_marks)
        marks.update({name: True for name in secret_names if name in updated})
        if marks != app.env_secret_marks:
            store.set_env_secret_marks(app.domain, marks)
            app.env_secret_marks = marks

        if not restart or app.is_static:
            return EnvChange(domain=app.domain, changed=True, restarted=False, previous=previous)

        log.substep(f"Restarting {app.domain} on its new database settings")
        healthy, evidence = restart_behind_gate(app, store, log)
        if healthy:
            return EnvChange(domain=app.domain, changed=True, restarted=True, previous=previous)

        if not restore_on_failure:
            raise DeploymentError(
                f"{app.domain} did not answer with its new database settings",
                details=evidence,
            )
        write_app_env(app, previous, logger=log)
        restored, _ = restart_behind_gate(app, store, log)
        state = "back and answering" if restored else "back, but it is not answering either"
        raise DeploymentError(
            f"{app.domain} did not answer with its new database settings; the previous "
            f"environment is {state}",
            details=evidence,
        )


def restore_app_env(
    app: App, previous: dict[str, str], *, store: NoustStore, restart: bool, log: Logger
) -> bool:
    """
    Put an application's previous variables back and restart it on them.

    Best effort, for a caller undoing a multi-application change: a failure
    is logged and reported, never raised over the error that caused the undo.

    Args:
        app: The application.
        previous: The variables to put back.
        store: The store, for the gate.
        restart: Restart the application on them.
        log: Where progress is reported.

    Returns:
        Whether the application is back and, when restarted, answering.
    """
    try:
        with app_lock(app.domain, "database settings rollback"):
            write_app_env(app, previous, logger=log)
            if not restart or app.is_static:
                return True
            healthy, _ = restart_behind_gate(app, store, log)
            return healthy
    except (NoustError, OSError) as exc:
        log.warning(f"Could not put back the environment of {app.domain}: {exc}")
        return False
