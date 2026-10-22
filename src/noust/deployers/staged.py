# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build a release without serving it, and promote it later without building it again (checklist B-18).

An update builds a release and switches to it in one go. Staging is the two halves
apart, which is what Vercel's "staged production deployment" and Heroku's pipelines give:

1. :func:`stage_build` fetches, installs and builds a new release exactly as an update would
   and stops. Production is untouched: ``current`` stays, no hook runs (a ``pre_deploy`` hook
   migrates a database, which is for when the release serves), nothing restarts. The release
   is recorded as ``built``.
2. :func:`promote_staged` makes a built release the one that serves. It is the activation
   a promotion between environments does on the receiving side
   (:meth:`~noust.deployers.base.BaseDeployer.deploy_prebuilt`): the release is linked to the
   application's own ``shared/``, its references are resolved, Prisma's migrations and the
   ``pre_deploy`` hooks run against the application's database, and it is activated behind the
   same health gate as any release, which puts back what served if it does not answer. Nothing is
   built a second time.

Both are the application's own operations: held by the application's lock, by a freeze of
deploys (:mod:`noust.core.deploy_guard`) and by the cancellation window of an update.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from noust.central import require_server_role
from noust.core import cancellation, deploy_guard
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, SourceError, ValidationError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.core.store import App, DeploymentTrigger
from noust.deployers import deploy_events
from noust.deployers.base import BaseDeployer
from noust.deployers.helpers.layout import RELEASES, app_root
from noust.deployers.helpers.release_build import StagedRelease, stage_release
from noust.deployers.lifecycle import (
    _confirm_release_schema,
    _forget_upstream,
    _release_app,
    _resolve_in_cache,
    branch_to_build,
    list_releases,
)
from noust.deployers.registry import get_deployer
from noust.deployers.releases import ReleaseManager
from noust.validators.domain import validate_domain
from noust.validators.source import validate_source


@dataclass(frozen=True)
class StagedBuild:
    """
    A release that was built and is waiting.

    Attributes:
        domain: The application.
        release_id: The release's id, the directory name.
        commit: The short commit it was built from.
    """

    domain: str
    release_id: str
    commit: str | None


@dataclass(frozen=True)
class Promoted:
    """
    What :func:`promote_staged` did.

    Attributes:
        domain: The application.
        release_id: The release now serving.
        previous_id: The release that served before, if any.
        deployment_id: The history row of the activation.
        warnings: Why it is deployed with warnings (a failed ``post_deploy`` hook).
    """

    domain: str
    release_id: str
    previous_id: str | None
    deployment_id: int | None
    warnings: str | None = None


def _stageable(app: App) -> BaseDeployer:
    """
    Find the deployer of an application that can stage a release.

    Args:
        app: The application.

    Returns:
        Its deployer, unconfigured.

    Raises:
        DeploymentError: It is not on the release layout, or its type does not build releases
            from source (a Compose stack, a container, a static site with no build).
    """
    if app.layout != RELEASES:
        raise DeploymentError(
            f"{app.domain} is not on the release layout",
            details=f"Only an application that builds releases can build one without serving "
            f"it: noust app migrate {app.domain}",
        )
    deployer = get_deployer(app.app_type or "auto", verbose=False)
    if not isinstance(deployer, BaseDeployer) or not getattr(deployer, "SUPPORTS_RELEASES", False):
        raise DeploymentError(
            f"{app.app_type} applications cannot build a release without serving it",
            details="Its releases are not built from source by Noust: update it instead.",
        )
    return deployer


def stage_build(
    domain: str,
    *,
    commit: str | None = None,
    branch: str | None = None,
    package_manager: str = "auto",
    trigger: str = DeploymentTrigger.CLI.value,
    job_id: str | None = None,
    on_step: Callable[[str], None] | None = None,
    logger: Logger | None = None,
    verbose: bool = False,
    no_cache: bool = False,
    announce: bool = True,
) -> StagedBuild:
    """
    Build the next release of an application and leave it waiting.

    Args:
        domain: The application's domain.
        commit: Build this commit instead of the head of the branch.
        branch: Build this branch; the one the application follows when None.
        package_manager: Node package manager, or ``auto``.
        trigger: Who asked, for the build's own record.
        job_id: The job driving it, when there is one.
        on_step: Called as each step begins.
        logger: Where the details go.
        verbose: Verbosity of the deployer.
        no_cache: Build from scratch.
        announce: Say that the release is built and how to promote it. Off for a caller that
            does something else with it (a bundle packs it and removes it).

    Returns:
        The release, built.

    Raises:
        NoustError: The application is unknown, cannot stage, has no source, or the build
            failed (the half-built release is removed).
        SourceError: A commit was asked of a source that is not git.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Applications")
    log = logger or Logger(verbose=verbose)
    domain = validate_domain(domain)
    app = _release_app(domain)
    deployer = _stageable(app)
    fetch_from = app.source
    if not fetch_from:
        raise NoustError(
            f"{domain} has no recorded source to build a release from",
            details="Update it with a source first: noust update DOMAIN --source URL",
        )
    if commit is not None and validate_source(fetch_from)[0] != "git":
        raise SourceError(
            f"{domain} is not deployed from git; there is no commit {commit} to build",
            details=f"Its source is {fetch_from}.",
        )
    follow = branch or branch_to_build(app)
    root = app_root(app)
    deployer.configure(
        domain=domain,
        source=fetch_from,
        port=app.port,
        app_path=root,
        branch=follow,
        package_manager=package_manager,
        trigger=trigger,
        job_id=job_id,
        no_cache=no_cache,
    )
    report = on_step or (lambda _message: None)
    try:
        with app_lock(domain, "staged build"), cancellation.operation_window(domain, "update"):
            if commit is not None:
                full = _resolve_in_cache(deployer.source_manager, fetch_from, root, follow, commit)
                with cancellation.interruptible("fetching the source"):
                    staged = stage_release(
                        fetch_from,
                        follow,
                        releases=deployer.releases,
                        source_manager=deployer.source_manager,
                        logger=log,
                        commit=full,
                    )
                deployer.adopt_release(staged)
            built = deployer.stage(on_step=report)
    finally:
        _forget_upstream(domain)
    if announce:
        log.success(
            f"Release {built.id} is built and waiting: noust releases promote {domain} {built.id}"
        )
    return StagedBuild(domain=domain, release_id=built.id, commit=built.short_commit)


def staged_releases(domain: str) -> list[StagedBuild]:
    """
    List the releases of an application that were built and never served.

    Args:
        domain: The application's domain.

    Returns:
        Them, newest first.

    Raises:
        NoustError: The application is unknown.
        DeploymentError: It is not on the release layout.
    """
    return [
        StagedBuild(domain=domain, release_id=info.id, commit=info.commit)
        for info in list_releases(domain)
        if info.waiting
    ]


def promote_staged(
    domain: str,
    release_id: str | None = None,
    *,
    trigger: str = DeploymentTrigger.CLI.value,
    job_id: str | None = None,
    on_step: Callable[[str], None] | None = None,
    logger: Logger | None = None,
    verbose: bool = False,
    frozen_ok: bool = False,
    schema_changed_ok: bool = False,
) -> Promoted:
    """
    Make a release that was built and is waiting the one that serves.

    Args:
        domain: The application's domain.
        release_id: The release; the newest one waiting when None.
        trigger: Who asked, recorded in the history.
        job_id: The job driving it, when there is one.
        on_step: Called as each step begins.
        logger: Where the details go.
        verbose: Verbosity of the deployer.
        frozen_ok: The operator confirmed deploying during a freeze of deploys.
        schema_changed_ok: The operator confirmed activating a release older than deployments
            that changed the database's schema.

    Returns:
        What was done.

    Raises:
        DeploysFrozenError: Deploys are frozen for the application and this was not confirmed.
        ValidationError: Nothing waits, the release is not one that waits, or it is serving.
        SchemaChangedError: The release is older than deployments that changed the schema.
        DeploymentError: A hook failed or the release did not pass the health gate (what
            served before is back by then).
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Applications")
    log = logger or Logger(verbose=verbose)
    domain = validate_domain(domain)
    deploy_guard.require_open(domain, override=frozen_ok)
    app = _release_app(domain)
    deployer = _stageable(app)
    waiting = staged_releases(domain)
    if release_id is None:
        if not waiting:
            raise ValidationError(
                f"{domain} has no release that was built and is waiting",
                details=f"Build one with: noust update {domain} --build-only",
                field="release",
            )
        release_id = waiting[0].release_id
    elif release_id not in {item.release_id for item in waiting}:
        raise ValidationError(
            f"Release {release_id} is not one that was built and is waiting",
            details="Waiting: "
            + (", ".join(item.release_id for item in waiting) or "none")
            + ". To switch to a release that already served: noust releases rollback "
            f"{domain} {release_id}",
            field="release",
        )
    root = app_root(app)
    manager = ReleaseManager(root, fs=get_fs(), logger=log)
    release = next((item for item in manager.list() if item.id == release_id), None)
    if release is None:
        raise DeploymentError(f"Release {release_id} is no longer on disk")
    previous = manager.current()
    deployer.configure(
        domain=domain,
        source=app.source,
        port=app.port,
        app_path=root,
        branch=branch_to_build(app),
        trigger=trigger,
        job_id=job_id,
    )
    try:
        with app_lock(domain, "promote a built release"):
            if not schema_changed_ok:
                _confirm_release_schema(app, release, previous)
            staged = StagedRelease(path=release.path, commit=release.commit, manager=manager)
            with deploy_events.operation("activate"):
                result = deployer.deploy_prebuilt(staged, on_step=on_step, apply_migrations=True)
    finally:
        _forget_upstream(domain)
    return Promoted(
        domain=domain,
        release_id=release_id,
        previous_id=previous.id if previous is not None else None,
        deployment_id=getattr(deployer, "last_deployment_id", None),
        warnings=getattr(result, "warnings", None),
    )


__all__ = [
    "Promoted",
    "StagedBuild",
    "promote_staged",
    "stage_build",
    "staged_releases",
]
