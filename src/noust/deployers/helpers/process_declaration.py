# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one place that makes a release's declared processes run beside its web process (checklist A-52, B-67).

A repository names a ``worker`` or a ``clock`` next to ``web`` (:mod:`noust.deployers.helpers.processes`).
Whatever makes a release the one that serves has to make those processes follow it: a worker left on
code the release it went back to does not have crashes, or runs yesterday's job against today's schema.
There are two ways a release becomes the active one and both end here:

- a deployment builds it (:class:`~noust.deployers.base.BaseDeployer` reads the tree it staged and
  calls :func:`declare_for_release`),
- an activation or a restore puts one back that already exists (:func:`declare_restored_release`: a
  rollback of the history, ``noust releases rollback``, ``noust update --commit`` onto a release that
  is still on disk, a rollback to a backup of an application on releases).

Either way the processes are recorded and their units written once ``current`` points at the release
and before the restart, which restarts every copy with the web unit and judges them with it; the
caller then commits the :class:`~noust.managers.app_processes.Declaration` when the release held and
rolls it back when it did not, so a release that is put back takes the processes it declared with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from noust.core import app_build_store
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers.helpers.build_config import project_dir, resolve_build
from noust.deployers.helpers.layout import RELEASES
from noust.deployers.helpers.processes import ProcessSet, resolve_processes

if TYPE_CHECKING:
    from noust.managers.app_processes import Declaration, ProcessManager

__all__ = [
    "declare_for_release",
    "declare_restored_release",
    "declare_restored_tree",
    "procfile_honoured",
    "read_release_processes",
    "runs_a_process",
]


def procfile_honoured(domain: str, store: NoustStore) -> bool:
    """
    Tell whether an application that exists starts and releases from its ``Procfile``.

    One that was deployed before Noust read a Procfile keeps starting the way it was deployed
    (reading it on an update would change a working application's start command and run a
    ``release`` command on a database nothing had ever migrated that way); one whose unit was
    written from it says so in its launch record.

    Args:
        domain: The application.
        store: The store holding the launch record.

    Returns:
        True when the unit was written from the Procfile.
    """
    applied = app_build_store.get_applied(domain, store=store)
    return bool(applied and applied.get("procfile"))


def runs_a_process(app: App) -> bool:
    """
    Tell whether an application's type runs a process of its own for others to run beside.

    A static site, a PHP pool, a container or a stack has none: the web server, FPM or Docker runs
    it. The same answer as the build block's ``start`` key being accepted by its type.

    Args:
        app: The application.

    Returns:
        True when its type has a unit that runs the application's command.
    """
    # Imported here: the deployers import this module.
    from noust.deployers.registry import DeployerRegistry, _import_deployers

    if app.is_static:
        return False
    _import_deployers()
    deployer = DeployerRegistry.get(app.app_type or "")
    return "start" in getattr(deployer, "BUILD_KEYS", frozenset())


def read_release_processes(app: App, tree: Path, store: NoustStore) -> ProcessSet:
    """
    Name the process types a release's tree declares.

    Read the way a deployment of the application would read them: from the build block's base
    directory, the operator's block over the repository's.

    Args:
        app: The application.
        tree: A release (or the running code) of the application.
        store: The store holding the operator's build block.

    Returns:
        What the tree declares; a Procfile that cannot be used is reported in
        :attr:`~noust.deployers.helpers.processes.ProcessSet.problem`.

    Raises:
        ValidationError: The ``noust.yaml`` that applies is not valid, or the base directory is a
            link or not there.
    """
    build = resolve_build(app, tree, store)
    return resolve_processes(project_dir(tree, build.base_directory))


def declare_for_release(
    manager: ProcessManager,
    domain: str,
    found: ProcessSet,
    *,
    procfile_applies: bool,
    releases_layout: bool,
    log: Logger,
) -> Declaration | None:
    """
    Record the processes a release declares besides ``web`` and ``release`` and write their units.

    Nothing is started: the health gate that activates the release restarts every copy with the web
    unit and judges them with it. The caller commits the declaration when the release holds and
    rolls it back when it does not. A process the operator defined under the same name is theirs
    and stays so.

    Never fails the activation: a Procfile that cannot be used, an application deployed in place
    (processes run from the releases layout), a declared process that cannot be a unit, or a unit
    that cannot be written is said in the log and the release is activated without it.

    Args:
        manager: The process manager of the application's store and services.
        domain: The application.
        found: The process types of the release being activated.
        procfile_applies: Whether this application reads its ``Procfile`` at all.
        releases_layout: Whether the application is on the releases layout.
        log: Where the declaration is reported.

    Returns:
        What to commit or roll back; None when there is nothing to judge.
    """
    if found.problem or (found.source == "Procfile" and not procfile_applies):
        # Not read, so not a reason to change what runs: the processes a previous
        # deployment declared stay as they are.
        return None
    beside = [process.name for process in found if not process.started_by_noust]
    if not releases_layout:
        if beside:
            log.warning(
                f"{', '.join(beside)} declared by the repository "
                f"{'is' if len(beside) == 1 else 'are'} not started: processes run on the "
                f"releases layout and this application is deployed in place "
                f"(noust app migrate {domain})"
            )
        return None
    try:
        declaration = manager.declare(domain, found)
    except NoustError as exc:
        log.warning(
            "The processes the repository declares are not started: "
            f"{exc.message}{'. ' + exc.details if exc.details else ''}"
        )
        return None
    for name, reason in declaration.skipped:
        log.warning(f"The {name} process the repository declares is not started: {reason}")
    if declaration.changed:
        log.substep(
            f"Processes beside the web one, from the repository: {', '.join(declaration.changed)}"
        )
    if declaration.removed:
        log.substep(f"The repository no longer declares: {', '.join(declaration.removed)}")
    return None if declaration.empty else declaration


def declare_restored_release(
    app: App,
    release: Path,
    *,
    store: NoustStore,
    log: Logger,
    services: object | None = None,
) -> Declaration | None:
    """
    Make the processes a release that already exists declares the ones that run, as a deployment does.

    What every way of putting a release back calls once ``current`` points at it and before the
    restart: the repository's process types are read from that release's own tree, not from the one
    that was serving, so a worker follows the code it was written for. The application's lock is
    held by the caller.

    Never fails the activation (see :func:`declare_for_release`).

    Args:
        app: The application's row.
        release: The release's directory.
        store: The store.
        log: Where the declaration is reported.
        services: The service manager the units are written through; the default one when None.

    Returns:
        What to commit when the release holds and roll back when it does not; None when there is
        nothing to judge.
    """
    # Imported here: the manager builds on the deployers, which import this module.
    from noust.managers import app_processes

    if not runs_a_process(app):
        return None
    try:
        found = read_release_processes(app, release, store)
    except ValidationError as exc:
        # A release is judged by the file it was built from; one that cannot be read now (it was
        # valid when it was built) is a reason to leave the processes as they are, not to fail.
        log.warning(f"The processes this release declares were not read: {exc.message}")
        return None
    if found.problem:
        log.warning(f"The Procfile is not used: {found.problem}")
    manager = app_processes.ProcessManager(store=store, services=services, log=log)
    return declare_for_release(
        manager,
        app.domain,
        found,
        procfile_applies=procfile_honoured(app.domain, store),
        releases_layout=app.layout == RELEASES,
        log=log,
    )


def declare_restored_tree(
    app: App,
    app_path: Path,
    *,
    store: NoustStore,
    log: Logger,
    services: object | None = None,
) -> Declaration | None:
    """
    Make the processes follow the release a restore of the application's tree brought back.

    A restore from a backup replaces the application directory, the ``workers`` link with it: the
    one it brings may point at a release the archive does not carry. The link is pointed at the
    release that serves, and that release's processes are declared as an activation does
    (:func:`declare_restored_release`), kept: the files are what is on disk now, and nothing puts
    the previous ones back when the health check that follows is not met.

    Args:
        app: The application's row.
        app_path: The application directory, restored.
        store: The store.
        log: Where the declaration is reported.
        services: The service manager the units are written through; the default one when None.

    Returns:
        The declaration, already committed; None when there is nothing to judge or the tree is
        not on the releases layout.
    """
    # Imported here: the releases module builds on the deployers that import this one.
    from noust.deployers.releases import ReleaseManager

    releases = ReleaseManager(app_path, logger=log)
    active = releases.current()
    if active is None or app.layout != RELEASES:
        return None
    try:
        if releases.workers_link.is_symlink():
            releases.point_workers(active.path)
    except NoustError as exc:
        log.warning(f"The processes were not pointed at release {active.id}: {exc.message}")
    declared = declare_restored_release(app, active.path, store=store, log=log, services=services)
    if declared is not None:
        declared.commit()
    return declared
