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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from noust.central import require_server_role
from noust.core.applock import app_lock
from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, get_store
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.layout import RELEASES
from noust.deployers.helpers.release_build import REPO_CACHE_DIR, StagedRelease
from noust.deployers.releases import ReleaseManager
from noust.managers.source_manager import SourceManager


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


def current_commit(app: App, source_manager: SourceManager) -> tuple[str, Path]:
    """
    Name the commit an application runs now, and the clone that has it.

    Args:
        app: Its row.
        source_manager: The source manager.

    Returns:
        The full commit id and the repository to export it from.

    Raises:
        ValidationError: It was not deployed from git, or it has no active
            release.
    """
    app_path = Path(app.app_path)
    if app.layout == RELEASES:
        active = ReleaseManager(app_path).current()
        repository = app_path / REPO_CACHE_DIR
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
) -> TrialResult:
    """
    Build an application's current commit in the sandbox, without activating it.

    Args:
        domain: The application.
        logger: Where the build's steps and output go.
        verbose: Verbosity of the deployer.
        store: The store; the process-wide one by default.
        fs: The filesystem; the process-wide one by default.

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
        deployer = get_deployer(app.app_type or "auto", verbose=verbose)
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
        commit, repository = current_commit(app, deployer.source_manager)
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
                domain, passed=False, commit=commit, detail=detail, store=store
            )
            return TrialResult(domain, False, commit, detail, state, source, uncommitted)
        finally:
            fs.remove_tree(scratch)
    state = build_sandbox.record_trial(domain, passed=True, commit=commit, detail=None, store=store)
    log.success(
        f"{domain} builds in the sandbox; enable it with: noust app sandbox enable {domain}"
    )
    return TrialResult(domain, True, commit, None, state, source, uncommitted)
