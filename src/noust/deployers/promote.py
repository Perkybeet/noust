# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Promoting a release from one application to another, without building it again.

An environment of a project (staging, production) is a place applications live
in, and the same service in two environments is two applications. A release is
immutable once built, so *promoting* staging's release to production is putting
that very release on production's application: it is copied into its
``releases/`` and activated there, behind the health gate and after production's
own ``pre_deploy`` hooks, with production's own variables, database and
persistent paths. Nothing is fetched, installed or built, so what runs in
production is what ran in staging.

What this does not carry is configuration: a release has none of its own. The
``.env`` is the target's; a framework that bakes variables into the code when it
builds (``NEXT_PUBLIC_*``, ``VITE_*``) keeps the source's values, which the plan
says. An application that needs the target's values in the build is updated
instead.

A release holds absolute paths of the application it was built in (a Python
virtual environment's interpreter, a framework's manifest). The copy is
rewritten to say the target's wherever the old root appears in a text file.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from noust.central import require_server_role
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.core.repositories import repository_key
from noust.core.runner import get_runner
from noust.core.store import App, DeploymentTrigger, get_store
from noust.deployers.base import BaseDeployer
from noust.deployers.helpers.app_env import read_app_env
from noust.deployers.helpers.layout import RELEASES, app_root
from noust.deployers.helpers.release_build import StagedRelease, discard_release
from noust.deployers.lifecycle import (
    _newest_deployment_of_commit,
    branch_to_build,
    require_schema_change_confirmed,
)
from noust.deployers.registry import get_deployer
from noust.deployers.releases import RELEASES_DIR, Release, ReleaseManager
from noust.deployers.unit_environment import BUILD_TIME_PREFIXES
from noust.validators.domain import validate_domain

#: Deadline of the copy of a release: a large ``node_modules`` is gigabytes.
COPY_TIMEOUT = 1800

#: Deadline of the search for the old root in the copy.
SEARCH_TIMEOUT = 600

#: Largest text file rewritten; a bundle bigger than this does not name a root.
MAX_REWRITE_BYTES = 32 * 1024 * 1024

#: Called as each phase begins: its position, the total and what it does.
PhaseReporter = Callable[[int, int, str], None]


@dataclass(frozen=True)
class PromotionPlan:
    """
    What a promotion will do, decided before anything is copied.

    Attributes:
        source: The application the release comes from.
        target: The application it goes to.
        release: The release.
        warnings: What the operator should know first.
    """

    source: App
    target: App
    release: Release
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Promotion:
    """
    What a promotion did.

    Attributes:
        source: The application's domain it came from.
        target: The application's domain it went to.
        release: The release id (the same in both).
        commit: The commit it was built from, when known.
        deployment_id: The target's deployment history row.
        warnings: What the operator should know.
    """

    source: str
    target: str
    release: str
    commit: str | None
    deployment_id: int | None
    warnings: tuple[str, ...] = ()


def _owner_ids(app: App) -> tuple[int | None, int | None]:
    return app.project_id, app.environment_id


def promotion_targets(domain: str) -> list[App]:
    """
    List the applications a release of one can be promoted to.

    Args:
        domain: The application the release is of.

    Returns:
        The applications of the same project, in other environments, built from
        the same repository, on the release layout, of the same type; none for
        a preview or one that is not on releases.
    """
    domain = validate_domain(domain)
    store = get_store()
    source = store.get_app(domain)
    if source is None or source.layout != RELEASES or source.preview_parent:
        return []
    key = repository_key(source.source)
    found = []
    for other in store.list_apps():
        if (
            other.domain != source.domain
            and other.project_id == source.project_id
            and other.environment_id != source.environment_id
            and other.layout == RELEASES
            and not other.preview_parent
            and other.app_type == source.app_type
            and (repository_key(other.source) == key if key else other.source == source.source)
        ):
            found.append(other)
    return sorted(found, key=lambda app: app.domain)


def plan_promotion(
    source_domain: str, target_domain: str, release_id: str | None = None
) -> PromotionPlan:
    """
    Check that a release can go from one application to another.

    Args:
        source_domain: The application the release is of.
        target_domain: The application it goes to.
        release_id: The release; the one that serves when None.

    Returns:
        The plan.

    Raises:
        ValidationError: An application is missing, is a preview, is not on the
            release layout, the two are not in the same project (or are in the
            same environment), are not the same type or repository, or the
            release does not exist or cannot be used.
    """
    require_server_role("Applications")
    source_domain = validate_domain(source_domain)
    target_domain = validate_domain(target_domain)
    if source_domain == target_domain:
        raise ValidationError("A release is promoted to another application, not to itself")
    store = get_store()
    source = store.get_app(source_domain)
    target = store.get_app(target_domain)
    if source is None or target is None:
        missing = source_domain if source is None else target_domain
        raise ValidationError(f"Application not found: {missing}", details="Run 'noust list'.")
    for app in (source, target):
        if app.preview_parent:
            raise ValidationError(
                f"{app.domain} is a preview", details="Previews are not promoted."
            )
        if app.layout != RELEASES:
            raise ValidationError(
                f"{app.domain} is not on the release layout",
                details="Only applications that build releases can promote or receive one "
                f"(noust app migrate {app.domain}).",
            )
    if source.app_type != target.app_type:
        raise ValidationError(
            f"{source.domain} is {source.app_type} and {target.domain} is {target.app_type}",
            details="A release runs on the type it was built for.",
        )
    if _owner_ids(source)[0] != _owner_ids(target)[0]:
        raise ValidationError(
            f"{source.domain} and {target.domain} are not in the same project",
            details="Promote between the environments of one project; move an application "
            "there first with 'noust app move'.",
        )
    if source.environment_id == target.environment_id:
        raise ValidationError(
            f"{source.domain} and {target.domain} are in the same environment",
            details="Promotion moves a release up from one environment to the next.",
        )
    key = repository_key(source.source)
    if (repository_key(target.source) if key else target.source) != (key or source.source):
        raise ValidationError(
            f"{source.domain} and {target.domain} are not built from the same repository",
            details="Promote a release to the application it is the staging of.",
        )
    manager = ReleaseManager(app_root(source))
    chosen = _choose(manager, source, release_id)
    warnings = _warnings(source, target)
    return PromotionPlan(source, target, chosen, warnings)


def _choose(manager: ReleaseManager, source: App, release_id: str | None) -> Release:
    """
    Args:
        manager: The source's releases.
        source: The source application.
        release_id: The release asked for, or None for the one that serves.

    Returns:
        The release.

    Raises:
        ValidationError: There is none, or it is not one that was ever served.
    """
    releases = manager.list()
    if release_id is None:
        active = manager.current()
        if active is None:
            raise ValidationError(
                f"{source.domain} has no release that serves",
                details="Deploy it first, or name a release with --release.",
            )
        return active
    for release in releases:
        if release.id == release_id:
            return release
    available = ", ".join(release.id for release in releases[:5]) or "none"
    raise ValidationError(
        f"{source.domain} has no release {release_id}",
        details=f"Its releases: {available}. 'noust releases list {source.domain}' shows them.",
    )


def _warnings(source: App, target: App) -> list[str]:
    """
    Args:
        source: The application the release is of.
        target: The application it goes to.

    Returns:
        What the promotion does not carry that the operator may expect.
    """
    warnings = []
    try:
        names = sorted(
            name for name in read_app_env(source) if name.startswith(BUILD_TIME_PREFIXES)
        )
    except (NoustError, OSError):
        names = []
    if names:
        warnings.append(
            f"{source.domain} sets {', '.join(names)}, which the build bakes into the code: the "
            f"release keeps {source.domain}'s values in {target.domain}. Update {target.domain} "
            "instead of promoting if it needs its own."
        )
    return warnings


def _copy(source: Path, target: Path) -> None:
    """
    Copy a release, as it is (links stay links), cheaply where the filesystem allows.

    Args:
        source: The release in the source's ``releases/``.
        target: Where it goes in the target's.

    Raises:
        DeploymentError: The copy failed.
    """
    runner = get_runner()
    get_fs().make_dir(target.parent)
    result = runner.run(
        ["cp", "-a", "--reflink=auto", str(source), str(target)], timeout=COPY_TIMEOUT
    )
    if not result.success:
        raise DeploymentError(
            f"Could not copy release {source.name} to {target.parent.parent.name}",
            details=result.stderr.strip() or "Check the disk space: noust health",
        )


def _rewrite_root(release: Path, old_root: str, new_root: str, logger: Logger) -> int:
    """
    Make a copied release say the target's application directory, not the source's.

    Args:
        release: The copied release.
        old_root: The source's application directory.
        new_root: The target's.
        logger: Where files left alone are reported.

    Returns:
        How many files were rewritten.
    """
    runner = get_runner()
    fs = get_fs()
    found = runner.run(
        ["grep", "-rIlZ", "-F", "--", old_root, str(release)], timeout=SEARCH_TIMEOUT
    )
    # 1 means nothing matched; anything above is grep failing.
    if found.exit_code not in (0, 1):
        raise DeploymentError(
            f"Could not look for {old_root} in the copied release",
            details=found.stderr.strip(),
        )
    pattern = re.compile(re.escape(old_root) + r"(?![\w.-])")
    rewritten = 0
    for name in (part for part in found.stdout.split("\0") if part):
        path = Path(name)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_REWRITE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
            changed = pattern.sub(new_root, text)
            if changed != text:
                fs.write_text(path, changed, mode=stat.S_IMODE(path.stat().st_mode))
                rewritten += 1
        except (OSError, UnicodeDecodeError):
            logger.warning(f"Left {path} as it was: it could not be read as text")
    return rewritten


def promote(
    source_domain: str,
    target_domain: str,
    *,
    release_id: str | None = None,
    schema_changed_ok: bool = False,
    trigger: str = DeploymentTrigger.CLI.value,
    job_id: str | None = None,
    on_phase: PhaseReporter | None = None,
    logger: Logger | None = None,
) -> Promotion:
    """
    Put a release of one application on another, and activate it there.

    Args:
        source_domain: The application the release is of.
        target_domain: The application it goes to.
        release_id: The release; the one that serves when None.
        schema_changed_ok: Confirm going back past a deployment of the target
            that changed its database's schema.
        trigger: Who asked, for the deployment history.
        job_id: The job driving it, when there is one.
        on_phase: Called as each phase begins.
        logger: Where the details go.

    Returns:
        What was done.

    Raises:
        ValidationError: See :func:`plan_promotion`.
        SchemaChangedError: The release is older than a deployment of the target
            that changed its schema, and that was not confirmed.
        AppBusyError: Another operation runs on either application.
        DeploymentError: The copy failed, or the release did not pass the
            target's health check (what served there is back by then).
    """
    log = logger or Logger()
    report = on_phase or (lambda _i, _n, _message: None)
    plan = plan_promotion(source_domain, target_domain, release_id)
    source, target, release = plan.source, plan.target, plan.release
    store = get_store()
    if release.commit:
        earlier = _newest_deployment_of_commit(store, target.domain, release.commit)
        if earlier is not None and not schema_changed_ok:
            require_schema_change_confirmed(
                target.domain,
                after_id=earlier,
                target=f"release {release.id}",
                schema_changed_ok=False,
            )
    for warning in plan.warnings:
        log.warning(warning)
    target_manager = ReleaseManager(app_root(target), fs=get_fs(), logger=log)
    destination = app_root(target) / RELEASES_DIR / release.id
    with app_lock(target.domain, "promote"), app_lock(source.domain, "promote"):
        active = target_manager.current()
        if active is not None and active.id == release.id:
            raise ValidationError(
                f"{target.domain} already runs release {release.id}",
                details="There is nothing to promote.",
            )
        if destination.exists():
            raise DeploymentError(
                f"{target.domain} already has a release {release.id}",
                details="It was built there; activate it with 'noust releases rollback'.",
            )
        report(1, 3, f"Copying release {release.id} to {target.domain}")
        try:
            _copy(release.path, destination)
            rewritten = _rewrite_root(
                destination, str(app_root(source)), str(app_root(target)), log
            )
        except NoustError:
            discard_release(destination, releases=target_manager, logger=log)
            raise
        log.substep(f"Copied {release.id}; {rewritten} file(s) now name {target.domain}")
        report(2, 3, f"Activating release {release.id} on {target.domain}")
        deployer = get_deployer(target.app_type, verbose=log.verbose)
        if not isinstance(deployer, BaseDeployer) or not getattr(
            deployer, "SUPPORTS_RELEASES", False
        ):
            discard_release(destination, releases=target_manager, logger=log)
            raise DeploymentError(
                f"{target.app_type} applications cannot receive a release",
                details="Update the application from its branch instead.",
            )
        deployer.configure(
            domain=target.domain,
            source=target.source,
            port=target.port,
            app_path=app_root(target),
            branch=branch_to_build(target),
            trigger=trigger,
            job_id=job_id,
        )
        staged = StagedRelease(path=destination, commit=release.commit, manager=target_manager)
        deployer.deploy_prebuilt(staged, on_step=lambda message: report(2, 3, message))
        report(3, 3, f"{target.domain} runs release {release.id}")
        return Promotion(
            source.domain,
            target.domain,
            release.id,
            release.commit,
            getattr(deployer, "last_deployment_id", None),
            tuple(plan.warnings),
        )
