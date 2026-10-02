# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A trial build in the sandbox: ``noust app sandbox test`` and its API twin.

An application from before 3.1 keeps building as root until an operator turns
the sandbox on, and turning it on blind would find out at the next deploy
that its build needed something the sandbox takes away (a private registry
reached with root's ``~/.npmrc``, a ``postinstall`` that writes outside the
release). The trial answers first: what the next sandboxed build would build
is copied into a scratch directory in its build cache and installed and built
exactly as an enabled sandbox would, then thrown away. Nothing is activated,
restarted or recorded in the deployment history; only the outcome is kept, and
:func:`noust.deployers.helpers.sandbox.enable` asks for a passing one.

What is copied depends on the layout, because that is what differs between
them. A release is built from a commit export, so the trial exports the active
release's commit. An in-place update builds in the served tree, uncommitted
changes made on the server included (a ``pnpm-workspace.yaml`` that allows a
dependency's build script, say), so the trial copies the tracked files as they
are in that tree - never ``node_modules`` or build output, never through a link
- and names the uncommitted ones: they build today, and are lost to anyone who
deploys the repository.

From 3.2 the same trial is also Noust's own, once, before the update of an
application still building as root (:func:`trial_before_update`): it tries
what that update is about to build, and the update builds in the sandbox when
the trial passed, as root as before when it did not.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from noust.central import require_server_role
from noust.core.applock import app_lock
from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.logger import Logger
from noust.core.runner import CommandRunner
from noust.core.store import App, NoustStore, get_store
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.layout import RELEASES
from noust.deployers.helpers.release_build import REPO_CACHE_DIR, StagedRelease
from noust.deployers.releases import ReleaseManager
from noust.managers.source_manager import SourceManager

if TYPE_CHECKING:
    from noust.core.notifications.context import NotificationContext
    from noust.core.notifications.model import Notification
    from noust.deployers.interface import AppDeployer


@dataclass(frozen=True)
class TrialResult:
    """
    What a trial build did.

    Attributes:
        domain: The application.
        passed: Whether it installed and built in the sandbox.
        commit: The commit it built, or the one the tree it copied is on.
        detail: The failure, with the build's own output; None when it passed.
        state: The application's regime afterwards, with the trial recorded.
        source: What it built: :data:`FROM_COMMIT` or :data:`FROM_TREE`.
        uncommitted: The tracked files of an in-place tree that differ from
            its commit, which the trial built as they are.
    """

    domain: str
    passed: bool
    commit: str | None
    detail: str | None
    state: build_sandbox.SandboxState
    source: str = "commit"
    uncommitted: tuple[str, ...] = ()


#: The trial built an export of the commit (an application on releases).
FROM_COMMIT = "commit"

#: The trial built a copy of the in-place tree's tracked files as they are.
FROM_TREE = "working tree"


def uncommitted_warning(files: tuple[str, ...] | list[str]) -> str:
    """
    Say that an in-place tree builds with changes the repository does not have.

    Args:
        files: The uncommitted files.

    Returns:
        The warning.
    """
    return (
        f"These local changes are not in the repository; commit them: {', '.join(files)}. "
        "The trial built them as they are, as an in-place update does, but a fresh "
        "deploy of the repository would not have them."
    )


def current_commit(
    app: App, source_manager: SourceManager, staged: str | None = None
) -> tuple[str, Path]:
    """
    Name the commit an application runs now, and the clone that has it.

    Args:
        app: Its row.
        source_manager: The source manager.
        staged: On releases, the full commit an update has just staged into
            the repository cache, to try instead of the active one: what the
            update is about to build.

    Returns:
        The full commit id and the repository to export it from.

    Raises:
        ValidationError: It was not deployed from git, or it has no active
            release.
    """
    app_path = Path(app.app_path)
    if app.layout == RELEASES:
        repository = app_path / REPO_CACHE_DIR
        if staged is not None and (repository / ".git").is_dir():
            return staged, repository
        active = ReleaseManager(app_path).current()
        if active is None or active.commit is None or not (repository / ".git").is_dir():
            raise ValidationError(
                f"{app.domain} has no active release built from git to try in the sandbox",
                details=f"Deploy it once, then test: noust update {app.domain}",
            )
        return source_manager.resolve_commit(repository, active.commit), repository
    info = source_manager.get_repo_info(app_path)
    if not info.get("is_git") or not info.get("commit"):
        raise ValidationError(
            f"{app.domain} is not a git checkout; there is no commit to try in the sandbox",
            details="Only applications deployed from git can be tested before enabling; "
            f"enable it with --force and the next update builds it in the sandbox: "
            f"noust app sandbox enable {app.domain} --force",
        )
    return source_manager.resolve_commit(app_path, str(info["commit"])), app_path


def run_trial(
    domain: str,
    *,
    logger: Logger | None = None,
    verbose: bool = False,
    store: NoustStore | None = None,
    fs: FileSystem | None = None,
    commit: str | None = None,
    make_deployer: Callable[[str], AppDeployer] | None = None,
    automatic: bool = False,
) -> TrialResult:
    """
    Build an application's current commit in the sandbox, without activating it.

    Args:
        domain: The application.
        logger: Where the build's steps and output go.
        verbose: Verbosity of the deployer.
        store: The store; the process-wide one by default.
        fs: The filesystem; the process-wide one by default.
        commit: On releases, the commit an update staged, tried instead of
            the active release's.
        make_deployer: Builds the deployer for the application's type; the
            registry's by default. An update passes one like its own.
        automatic: Noust runs it by itself before an update; recorded so.

    Returns:
        What happened, also recorded on the application.

    Raises:
        ValidationError: Unknown application, one whose builds do not run in
            the sandbox, one with no commit to build, or a Noust that is not
            running as root (the sandbox would not be tried at all).
        AppBusyError: Another operation is running on the application.
    """
    from noust.deployers.base import BaseDeployer
    from noust.deployers.monorepo import MonorepoDeployer
    from noust.deployers.registry import get_deployer

    require_server_role("Applications")
    store = store or get_store()
    fs = fs or get_fs()
    log = logger or Logger(verbose=verbose)
    app = store.get_app(domain)
    if app is None:
        raise ValidationError(
            f"Application not found: {domain}", details="See the applications with: noust list"
        )
    build_sandbox.refuse_unsupported(app)
    if not build_sandbox.running_as_root():
        raise ValidationError(
            "A trial build in the sandbox needs root",
            details="The sandbox hands the build to the noust-build account, which only root "
            "can do. Run the command as root.",
        )

    with app_lock(domain, "sandbox test"):
        build = make_deployer or (lambda app_type: get_deployer(app_type, verbose=verbose))
        deployer = build(app.app_type or "auto")
        if not isinstance(deployer, (BaseDeployer, MonorepoDeployer)):
            raise ValidationError(
                f"{domain} is a {app.app_type} application, whose builds do not run in the sandbox"
            )
        app_path = Path(app.app_path)
        deployer.configure(
            domain=app.domain,
            source=app.source or str(app_path),
            port=app.port,
            app_path=app_path,
            branch=app.branch,
        )
        deployer.logger = log  # type: ignore[assignment]
        commit, repository = (
            current_commit(app, deployer.source_manager, commit)
            if commit is not None
            else current_commit(app, deployer.source_manager)
        )
        source = FROM_COMMIT if app.layout == RELEASES else FROM_TREE
        if source == FROM_COMMIT:
            log.info(f"Trying commit {commit[:7]} of {domain} in the sandbox; nothing is activated")
        else:
            log.info(
                f"Trying the tree of {domain} (commit {commit[:7]} and its uncommitted "
                "changes, as an in-place update builds it) in the sandbox; nothing is activated"
            )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        scratch = build_sandbox.cache_dir_for(deployer.app_name) / (
            f"{build_sandbox.TRIAL_DIR}-{stamp}"
        )
        fs.make_dir(scratch, mode=0o755)
        uncommitted: tuple[str, ...] = ()
        try:
            if source == FROM_COMMIT:
                deployer.source_manager.export_commit(repository, commit, scratch)
            else:
                uncommitted = tuple(deployer.source_manager.export_worktree(repository, scratch))
                if uncommitted:
                    log.warning(uncommitted_warning(uncommitted))
            deployer.sandbox_trial(
                StagedRelease(path=scratch, commit=commit, manager=ReleaseManager(app_path))
            )
        except NoustError as exc:
            detail = f"{exc.message}\n{exc.details}".strip() if exc.details else exc.message
            log.error(f"The trial build failed in the sandbox: {exc.message}")
            state = build_sandbox.record_trial(
                domain,
                passed=False,
                commit=commit,
                detail=detail,
                store=store,
                automatic=automatic,
            )
            return TrialResult(domain, False, commit, detail, state, source, uncommitted)
        finally:
            fs.remove_tree(scratch)
    state = build_sandbox.record_trial(
        domain, passed=True, commit=commit, detail=None, store=store, automatic=automatic
    )
    if not automatic:
        log.success(
            f"{domain} builds in the sandbox; enable it with: noust app sandbox enable {domain}"
        )
    return TrialResult(domain, True, commit, None, state, source, uncommitted)


#: Who turns the sandbox on when a trial before an update passes.
AUTOMATIC_ACTOR = "noust"


def trial_before_update(
    domain: str,
    *,
    store: NoustStore,
    logger: Logger,
    runner: CommandRunner,
    commit: str | None,
    make_deployer: Callable[[str], AppDeployer],
    fs: FileSystem | None = None,
) -> build_sandbox.SandboxState | None:
    """
    Try an application still building as root in the sandbox, before an update builds it.

    The sandbox by default (3.2). Called by the update once the source it is
    about to build is in place (pulled in place, staged on releases), so the
    trial builds exactly that. A passing trial turns the sandbox on and the
    update builds in it; a failing one is recorded, warned about and notified,
    and the update builds as root exactly as it did. Never raises: whatever
    goes wrong here, the update goes on as it would have.

    Nothing is tried, and nothing changes, for an application whose regime is
    not ``legacy`` (on already, or off by an operator's recorded decision),
    one Noust already tried by itself, a type with nothing to build or that
    builds in Docker, a Noust that is not root, a rehearsal, or a server where
    the sandbox does not hold (containers, WSL without mount namespaces).

    Args:
        domain: The application.
        store: The store.
        logger: The update's logger; the trial's output goes into its log.
        runner: The runner, for the build account and the self-test.
        commit: On releases, the commit the update staged; None in place.
        make_deployer: Builds a deployer like the update's own for a type.
        fs: The filesystem; the process-wide one by default.

    Returns:
        The new regime when the sandbox was turned on; None otherwise.
    """
    if not build_sandbox.running_as_root() or is_rehearsal():
        return None
    app = store.get_app(domain)
    if app is None or (app.app_type or "") in (
        build_sandbox.UNSUPPORTED_TYPES | build_sandbox.NOTHING_TO_BUILD_TYPES
    ):
        return None
    try:
        state = build_sandbox.get_state(domain, store=store)
    except (NoustError, sqlite3.Error) as exc:
        logger.warning(
            f"The build regime of {domain} could not be read ({exc}); building as before"
        )
        return None
    if state.mode != build_sandbox.SandboxMode.LEGACY.value or state.auto_trial_at is not None:
        return None
    try:
        build_sandbox.ensure_build_account(runner)
        holds = build_sandbox.self_test(runner, fs)
    except (NoustError, OSError) as exc:
        logger.substep(f"The build sandbox could not be checked here ({exc}); building as before")
        return None
    if not holds.passed:
        logger.substep(
            "The build sandbox does not hold on this server, so this update builds as before"
        )
        return None

    logger.info(
        f"{domain} still builds as root; trying this build in the sandbox first, "
        "where Noust 3.2 builds every application"
    )
    try:
        result = run_trial(
            domain,
            logger=logger,
            store=store,
            fs=fs,
            commit=commit,
            make_deployer=make_deployer,
            automatic=True,
        )
    except (NoustError, OSError, sqlite3.Error) as exc:
        # Nothing to try (not a git checkout, no active release) or nothing
        # could be prepared: not the sandbox's verdict, so nothing is recorded
        # and the next update tries again.
        logger.substep(f"The sandbox could not be tried ({exc}); building as before")
        return None
    if result.passed:
        enabled = build_sandbox.enable(domain, actor=AUTOMATIC_ACTOR, store=store)
        logger.success(f"{domain} built in the sandbox; it builds there from now on")
        return enabled
    logger.warning(
        f"{domain} did not build in the sandbox, so this update builds as root, as before. "
        f"Fix the build and test again with: noust app sandbox test {domain}; or record why "
        f"it needs root: noust app sandbox disable {domain} --reason '...'"
    )
    _notify_trial_failed(domain, result.commit, result.detail)
    return None


def _notify_trial_failed(domain: str, commit: str | None, detail: str | None) -> None:
    """
    Tell the operator an application's trial in the sandbox failed before its update.

    Args:
        domain: The application.
        commit: The commit tried.
        detail: The build's own output, verbatim.
    """
    from noust.core.notifier import notify_composed

    notify_composed(
        lambda ctx: compose_sandbox_trial_failed(domain, ctx, commit=commit, detail=detail)
    )


def compose_sandbox_trial_failed(
    domain: str,
    ctx: NotificationContext,
    *,
    commit: str | None = None,
    detail: str | None = None,
) -> Notification:
    """
    Compose the notification for a trial in the sandbox that failed before an update.

    Under the ``deploy_failed`` switch, as a warning: a build failed, the
    update itself went on as root.

    Args:
        domain: The application.
        ctx: The context.
        commit: The commit tried.
        detail: The build's own output, verbatim.

    Returns:
        The notification.
    """
    from noust.core.messages import message
    from noust.core.notifications.composers import build
    from noust.core.notifications.excerpt import make_excerpt
    from noust.core.notifications.model import Fact, State

    code = "sandbox.trial_failed"
    facts = []
    if commit:
        facts.append(Fact("commit", message("fact.commit", ctx.locale), commit[:7], True))
    return build(
        ctx,
        kind="deploy_failed",
        code=code,
        state=State.WARNING,
        subject=domain,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=Fact(
            "inspect",
            message("fact.inspect", ctx.locale),
            f"noust app sandbox status {domain}",
            True,
        ),
        excerpt=make_excerpt(
            detail or "",
            label=message("excerpt.output", ctx.locale),
            pin_error=True,
        ),
        path=f"/apps/{domain}",
        domain=domain,
    )
