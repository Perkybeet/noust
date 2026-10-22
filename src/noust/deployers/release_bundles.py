# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Building a release on one server and running it on another (checklist A-49).

A small VPS that runs an application is a poor place to compile it: a Next.js build wants more
memory than the application ever will. :func:`build_and_pack` runs on the server that builds. It
does what an update does up to the switch (fetch, install, build, in the build sandbox, as
``noust-build``), but no migration, no hook and no activation, because the release is not for
that server: the tree is packed into a bundle (:mod:`noust.deployers.bundle`) and the release the
build made is removed again, so it cannot be listed, pruned around or rolled back to there.
:func:`receive` runs on the server that serves. It checks the bundle against the checksum the
operator brought, extracts it with the archive extraction's checks, rewrites the builder's
directory in text files to its own, and activates the release as a promoted one is
(:meth:`~noust.deployers.base.BaseDeployer.deploy_prebuilt`): behind the health gate, after the
application's own ``pre_deploy`` hooks, with its own ``.env``, database and persistent paths. A
release that does not answer is put back and removed.

The application is registered on both servers (the builder needs its source, its variables and
its build settings; the receiver its domain, its site and its database), and they are the same
type. How the file travels is not decided here: ``scp``, a CI artifact or a fleet's tunnel all
do, because what makes it trustworthy is the checksum and the extraction, not the road.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from noust.central import require_server_role
from noust.core import audit, deploy_guard
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.core.store import App, DeploymentTrigger, get_store, is_compose_backed
from noust.deployers import bundle as bundles
from noust.deployers import deploy_events
from noust.deployers.base import BaseDeployer
from noust.deployers.bundle import Bundle, Manifest
from noust.deployers.helpers.layout import RELEASES, app_root
from noust.deployers.helpers.release_build import StagedRelease, discard_release
from noust.deployers.lifecycle import (
    _forget_upstream,
    _newest_deployment_of_commit,
    branch_to_build,
    require_schema_change_confirmed,
)
from noust.deployers.promote import _rewrite_root
from noust.deployers.registry import get_deployer
from noust.deployers.releases import RELEASES_DIR, ReleaseManager
from noust.deployers.staged import stage_build
from noust.validators.domain import validate_domain

__all__ = ["Reception", "build_and_pack", "receive"]

#: Called as each phase begins: its position, the total and what it does.
PhaseReporter = Callable[[int, int, str], None]


@dataclass(frozen=True)
class Reception:
    """
    What receiving a bundle did.

    Attributes:
        domain: The application that now runs it.
        release: The release id.
        commit: The commit it was built from, when known.
        deployment_id: The application's deployment history row.
        manifest: What the bundle said about itself.
        rewritten: How many text files named the builder's directory and now name this one's.
    """

    domain: str
    release: str
    commit: str | None
    deployment_id: int | None
    manifest: Manifest
    rewritten: int = 0


def _require_bundleable(app: App) -> None:
    """
    Refuse an application whose releases cannot travel as bundles.

    Args:
        app: The application's row.

    Raises:
        ValidationError: It is a preview, runs as a stack or a container, or builds in place.
    """
    if app.preview_parent:
        raise ValidationError(
            f"{app.domain} is a preview",
            details=f"A preview is built from its pull request; use {app.preview_parent}.",
        )
    if is_compose_backed(app.app_type):
        raise ValidationError(
            f"{app.domain} runs as {app.app_type}, which builds images, not releases",
            details="A stack and a container are built where they run, or from a registry.",
        )
    if app.layout != RELEASES:
        raise ValidationError(
            f"{app.domain} is not on the release layout",
            details=f"Only an application that builds releases can send or receive one: "
            f"noust app migrate {app.domain}",
        )


def _deployer_for(app: App, *, verbose: bool = False) -> BaseDeployer:
    """
    Make the deployer of an application's type, which must build releases.

    Args:
        app: The application's row.
        verbose: Verbosity of the deployer.

    Returns:
        The deployer, not yet configured.

    Raises:
        DeploymentError: The type does not build releases.
    """
    deployer = get_deployer(app.app_type, verbose=verbose)
    if not isinstance(deployer, BaseDeployer) or not getattr(deployer, "SUPPORTS_RELEASES", False):
        raise DeploymentError(
            f"{app.app_type} applications do not build releases that can travel",
            details="Update the application where it runs instead.",
        )
    return deployer


def build_and_pack(
    domain: str,
    destination: Path,
    *,
    commit: str | None = None,
    release_id: str | None = None,
    keep: bool = False,
    logger: Logger | None = None,
    verbose: bool = False,
    on_phase: PhaseReporter | None = None,
) -> Bundle:
    """
    Build a release of an application without activating it, and pack it as a bundle.

    The build is the staged one (:func:`~noust.deployers.staged.stage_build`): fetch, install and
    build exactly as an update would, in the build sandbox, with no hook, no migration, no switch
    and no deployment in the history. What is packed is that release, which is then removed so
    it cannot be promoted here; with ``keep`` it stays, built and waiting.

    Args:
        domain: The application, registered on this server.
        destination: The file to write; its checksum is written beside it.
        commit: Build this commit instead of the head of the branch (a git source only).
        release_id: Pack this release, already built here, instead of building one.
        keep: Leave the release this built in ``releases/`` instead of removing it once packed.
        logger: Where the build's steps go.
        verbose: Verbosity of the deployer.
        on_phase: Called as each phase begins.

    Returns:
        The bundle.

    Raises:
        ValidationError: The application cannot send releases, the release asked for does not
            exist, or the tree holds a link that points out of it.
        SourceError: A commit was asked of a source that is not git.
        DeploymentError: The build failed (its own output is carried).
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Applications")
    log = logger or Logger(verbose=verbose)
    report = on_phase or (lambda _i, _n, _message: None)
    domain = validate_domain(domain)
    store = get_store()
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    _require_bundleable(app)
    if release_id is not None and commit is not None:
        raise ValidationError(
            "Name a release or a commit, not both",
            details="A release already built has its commit; --commit builds a new one.",
        )
    root = app_root(app)
    manager = ReleaseManager(root, fs=get_fs(), logger=log)
    with app_lock(domain, "bundle"):
        built: str | None = None
        try:
            if release_id is not None:
                report(1, 2, f"Packing release {release_id}")
            else:
                report(1, 2, f"Building {domain}")
                built = stage_build(
                    domain,
                    commit=commit,
                    trigger=DeploymentTrigger.CLI.value,
                    on_step=log.substep,
                    logger=log,
                    verbose=verbose,
                    announce=False,
                ).release_id
            identifier = built or release_id or ""
            chosen = next((r for r in manager.list() if r.id == identifier), None)
            if chosen is None:
                available = ", ".join(r.id for r in manager.list()[:5]) or "none"
                raise ValidationError(
                    f"{domain} has no release {identifier}",
                    details=f"Its releases: {available}. 'noust releases list {domain}' shows them.",
                )
            report(2, 2, "Packing the release")
            builder, version = bundles.describe_builder()
            manifest = Manifest(
                format=bundles.FORMAT,
                app_type=app.app_type,
                domain=domain,
                release_id=chosen.id,
                commit=chosen.commit,
                branch=branch_to_build(app),
                app_root=str(root),
                built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                builder=builder,
                noust_version=version,
                files=0,
                bytes=0,
            )
            packed = bundles.pack(chosen.path, manifest, destination, shared=manager.shared_dir)
        finally:
            if built is not None and not keep:
                _forget_built(app, manager, built, log)
    audit.record(
        "releases.bundle",
        target=f"app:{domain}",
        details={
            "release": packed.manifest.release_id,
            "commit": packed.manifest.commit,
            "sha256": packed.sha256,
            "size": packed.size,
        },
    )
    return packed


def _forget_built(app: App, manager: ReleaseManager, release_id: str, log: Logger) -> None:
    """
    Remove the release a bundle was built from: it was never meant to serve here.

    Args:
        app: The application.
        manager: Its releases.
        release_id: The release the build made.
        log: Where a removal that fails is said.
    """
    release = next((r for r in manager.list() if r.id == release_id), None)
    if release is not None:
        discard_release(release.path, releases=manager, logger=log)
    try:
        get_store().delete_releases(app.id or 0, [release_id])
    except sqlite3.Error as exc:
        log.warning(f"Could not forget release {release_id}: {exc}")


def receive(
    domain: str,
    bundle_path: Path,
    *,
    sha256: str,
    schema_changed_ok: bool = False,
    frozen_ok: bool = False,
    trigger: str = DeploymentTrigger.CLI.value,
    job_id: str | None = None,
    on_phase: PhaseReporter | None = None,
    logger: Logger | None = None,
) -> Reception:
    """
    Put a release that was built elsewhere on an application here, and activate it.

    Args:
        domain: The application that will run it, registered on this server.
        bundle_path: The bundle.
        sha256: Its checksum, which the operator brought; the bundle is not read without it.
        schema_changed_ok: Confirm going back past a deployment that changed the schema.
        frozen_ok: Confirm deploying during a freeze of deploys.
        trigger: Who asked, for the deployment history.
        job_id: The job driving it, when there is one.
        on_phase: Called as each phase begins.
        logger: Where the details go.

    Returns:
        What was done.

    Raises:
        DeploysFrozenError: Deploys are frozen for the application and this was not confirmed.
        ValidationError: The checksum is wrong, the file is not a bundle, it is for another
            application type, the application cannot receive releases, or it runs this
            release already.
        SourceError: A member of the bundle is unsafe to extract.
        SchemaChangedError: The release is older than a deployment of this application that
            changed its schema, and that was not confirmed.
        DeploymentError: The release did not pass the health check (what served is back by
            then), or the application's type does not build releases.
        AppBusyError: Another operation is running on the application.
    """
    require_server_role("Applications")
    log = logger or Logger()
    report = on_phase or (lambda _i, _n, _message: None)
    domain = validate_domain(domain)
    deploy_guard.require_open(domain, override=frozen_ok)
    store = get_store()
    target = store.get_app(domain)
    if target is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    _require_bundleable(target)
    root = app_root(target)

    manager = ReleaseManager(root, fs=get_fs(), logger=log)

    def judged(manifest: Manifest) -> None:
        """Refuse a release this application cannot take, before any of it is extracted."""
        if manifest.app_type != target.app_type:
            raise ValidationError(
                f"This bundle was built as {manifest.app_type} and {domain} is {target.app_type}",
                details="A release runs on the type it was built for.",
            )
        active = manager.current()
        if active is not None and active.id == manifest.release_id:
            raise ValidationError(
                f"{domain} already runs release {manifest.release_id}",
                details="There is nothing to receive.",
            )
        if (root / RELEASES_DIR / manifest.release_id).exists():
            raise DeploymentError(
                f"{domain} already has a release {manifest.release_id}",
                details="It was built here; activate it with 'noust releases rollback'.",
            )
        if manifest.commit and not schema_changed_ok:
            earlier = _newest_deployment_of_commit(store, domain, manifest.commit)
            if earlier is not None:
                require_schema_change_confirmed(
                    domain,
                    after_id=earlier,
                    target=f"release {manifest.release_id}",
                    schema_changed_ok=False,
                )

    with app_lock(domain, "bundle receive"):
        report(1, 3, f"Checking and extracting {bundle_path.name}")
        manifest, destination = bundles.unpack(
            bundle_path, root / RELEASES_DIR, sha256=sha256, check=judged
        )
        rewritten = 0
        try:
            if manifest.app_root != str(root):
                rewritten = _rewrite_root(destination, manifest.app_root, str(root), log)
        except NoustError:
            discard_release(destination, releases=manager, logger=log)
            raise
        log.substep(
            f"Extracted {manifest.release_id}"
            + (f"; {rewritten} file(s) now name {domain}" if rewritten else "")
        )
        report(2, 3, f"Activating release {manifest.release_id} on {domain}")
        deployer = _deployer_for(target, verbose=log.verbose)
        deployer.configure(
            domain=domain,
            source=target.source,
            port=target.port,
            app_path=root,
            branch=branch_to_build(target),
            trigger=trigger,
            job_id=job_id,
        )
        staged = StagedRelease(path=destination, commit=manifest.commit, manager=manager)
        try:
            with deploy_events.operation("activate"):
                # The release has not met this application's database: Prisma's migrations,
                # which an update applies as it activates, are applied now (hooks win).
                deployer.deploy_prebuilt(
                    staged, on_step=lambda message: report(2, 3, message), apply_migrations=True
                )
        finally:
            _forget_upstream(domain)
        report(3, 3, f"{domain} runs release {manifest.release_id}")
    audit.record(
        "releases.receive",
        target=f"app:{domain}",
        details={
            "release": manifest.release_id,
            "commit": manifest.commit,
            "built_on": manifest.builder,
            "sha256": sha256.strip().lower(),
        },
    )
    return Reception(
        domain=domain,
        release=manifest.release_id,
        commit=manifest.commit,
        deployment_id=getattr(deployer, "last_deployment_id", None),
        manifest=manifest,
        rewritten=rewritten,
    )
