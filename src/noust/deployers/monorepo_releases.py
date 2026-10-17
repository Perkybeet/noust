# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A monorepo on the release layout, with instant rollback (checklist B-16 and A-53).

A monorepo used to be rebuilt in place: the tree its workspaces run from is the tree the update
installs and builds in, so going back after a bad update meant checking out the previous commit and
building it again, with every workspace down or on the new build while it did. On the release
layout every update builds a directory of its own (``releases/<id>``), the units run from
``current``, and going back is the swap of one link and a restart behind the same health gate.

What stays the monorepo's own, and why this is not :class:`~noust.deployers.base.BaseDeployer`:

- **Several units, one release.** Each workspace runs as its own unit with its own port, and the
  whole repository is the release. Activating a release restarts every unit and probes each one
  (:class:`~noust.deployers.helpers.workspace_gates.WorkspaceGates`); when any does not answer,
  ``current`` goes back to the release that served and every unit is restarted on it.
- **The workspaces' environment files travel by copy.** ``apps/web/.env.production`` is written by
  the deployment, not by the repository, and the unit loads it. A new release gets a copy of the
  one the active release has, so a release keeps the configuration it was running with and a
  rollback restores it. The application's ``.env`` is the usual link into ``shared/``.
- **Nothing converts a monorepo implicitly.** It moves onto releases only through
  ``noust app migrate`` (:mod:`noust.deployers.migrate`), as every in-place application does, and
  a redeploy of one that is on releases is refused: it is updated.

The build runs in the sandbox as ``noust-build`` in the new release, with ``shared/`` visible
read-only; the release is handed to the units' account after the build, like any other release.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from noust.core import cancellation
from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.fs import SECRET_MODE
from noust.core.store import App, ReleaseStatus, Service
from noust.deployers.helpers.layout import RELEASES
from noust.deployers.helpers.release_book import ReleaseBook
from noust.deployers.helpers.release_build import StagedRelease, discard_release, stage_release
from noust.deployers.helpers.workspace_gates import WorkspaceRestart
from noust.deployers.releases import CURRENT_LINK, ReleaseManager

if TYPE_CHECKING:
    from noust.deployers.interface import StepReporter, UpdateResult
    from noust.deployers.monorepo import MonorepoDeployer

__all__ = ["ENV_FILE_NAME", "MonorepoReleases", "workspace_paths"]

#: The file a workspace's unit loads, in the workspace's directory.
ENV_FILE_NAME = ".env.production"

#: Releases kept when the application records no retention.
DEFAULT_KEEP = 5


def workspace_paths(app: App | None, units: list[Service] | None = None) -> list[str]:
    """
    List the directories of an application's workspaces, relative to its tree.

    The deployment records them as JSON beside the application's variables, and each workspace's
    unit runs in its directory, so both are read: the record may be missing on an application
    that was deployed by an old version, and the unit may not be recorded yet.

    Args:
        app: The application.
        units: The units the store records for it.

    Returns:
        Relative POSIX paths, each a plain path inside the tree (no ``..``, no root), without
        repeats, in the order they were recorded.
    """
    found: list[str] = []
    recorded = (app.env_vars or {}).get("_workspaces") if app is not None else None
    if recorded:
        try:
            for entry in json.loads(recorded):
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    found.append(entry["path"])
        except (ValueError, TypeError):
            found = []
    root = Path(app.app_path) if app is not None and app.app_path else None
    for unit in units or []:
        if root is None or not unit.working_directory:
            continue
        directory = Path(unit.working_directory)
        for base in (root / CURRENT_LINK, root):
            if directory.is_relative_to(base):
                found.append(directory.relative_to(base).as_posix())
                break
    clean: list[str] = []
    for path in found:
        parts = PurePosixPath(path)
        if parts.is_absolute() or ".." in parts.parts or str(parts) in (".", ""):
            continue
        if str(parts) not in clean:
            clean.append(str(parts))
    return clean


class MonorepoReleases:
    """
    The release layout of one monorepo deployment.

    Args:
        deployer: The monorepo deployer this works for; it owns the build commands, the sandbox
            and the hooks, and this owns where the build happens and how it is switched in.
    """

    def __init__(self, deployer: MonorepoDeployer) -> None:
        self.deployer = deployer
        self.staged: StagedRelease | None = None
        self._manager: ReleaseManager | None = None

    # -- what the application is ---------------------------------------------------------------

    @property
    def manager(self) -> ReleaseManager:
        """The application's releases."""
        if self._manager is None:
            self._manager = ReleaseManager(
                self.deployer.app_path, fs=self.deployer.fs, logger=self.deployer.logger
            )
        return self._manager

    def enabled(self) -> bool:
        """
        Tell whether the application is on the release layout.

        Returns:
            True when its row says so. Read from the store each time: the layout is written by
            the migration, never by this deployment.
        """
        app = self.app()
        return app is not None and app.layout == RELEASES

    def app(self) -> App | None:
        """
        Returns:
            The application's row, or None when it has none.
        """
        deployer = self.deployer
        return deployer.store.get_app(deployer.domain) if deployer.domain else None

    def book(self) -> ReleaseBook:
        """
        Returns:
            The writer of the application's release rows.
        """
        deployer = self.deployer
        return ReleaseBook(deployer.store, deployer.logger, deployer.fs, deployer.domain)

    def keep(self) -> int:
        """
        Returns:
            How many releases the application keeps on disk, never less than one.
        """
        app = self.app()
        return max(1, (app.keep_releases if app is not None else 0) or DEFAULT_KEEP)

    def persistent_paths(self) -> list[str]:
        """
        Returns:
            The paths every release shares through ``shared/``, as the migration recorded them.
        """
        app = self.app()
        return list(app.persistent_paths) if app is not None else []

    # -- building a release ---------------------------------------------------------------------

    def adopt(self, staged: StagedRelease) -> None:
        """
        Build this release instead of staging a new one.

        What a deployment of one commit does: the caller exported that commit into a release.

        Args:
            staged: The release, exported and not yet built.
        """
        self.staged = staged
        self.deployer._build_root = staged.path

    def require(self) -> StagedRelease:
        """
        Returns:
            The release being built.

        Raises:
            DeploymentError: No release was staged, which means a step ran out of order.
        """
        if self.staged is None:
            raise DeploymentError(
                "No release is being built",
                details="The fetch step stages the release; it has to run first.",
            )
        return self.staged

    def stage(self) -> StagedRelease:
        """
        Put the source in a new release and link it to what releases share.

        The ``.env`` and the persistent paths are linked before anything is installed or built,
        because both may need them, and the workspaces' environment files are copied from the
        release that serves.

        Returns:
            The release.
        """
        deployer = self.deployer
        if self.staged is None:
            deployer.logger.substep(f"Source: {deployer.source}")
            with cancellation.interruptible("fetching the source"):
                self.staged = stage_release(
                    deployer.source,
                    deployer.branch,
                    releases=self.manager,
                    source_manager=deployer.source_manager,
                    logger=deployer.logger,
                )
        staged = self.staged
        deployer._build_root = staged.path
        self.book().record_row(staged)
        links = self.manager.link_shared(staged.path, self.persistent_paths())
        for path in links.linked:
            deployer.logger.substep(f"Linked {path} to shared/{path}")
        if links.conflicts:
            raise DeploymentError(
                f"Could not link {', '.join(links.conflicts)} into release {staged.id}",
                details="The repository has something where a shared path must go.",
            )
        self.carry_environment(staged)
        return staged

    def carry_environment(self, staged: StagedRelease) -> list[str]:
        """
        Copy the workspaces' environment files from the release that serves into a new one.

        Args:
            staged: The new release.

        Returns:
            The paths copied.
        """
        deployer = self.deployer
        fs = deployer.fs
        active = self.manager.current()
        if active is None or active.path == staged.path:
            return []
        units = [s for s in deployer.store.list_services() if self._owns(s)]
        copied: list[str] = []
        for relative in workspace_paths(self.app(), units):
            source = active.path / relative / ENV_FILE_NAME
            target = staged.path / relative / ENV_FILE_NAME
            # A link is a repository's doing, not ours: it could point out of the release.
            if source.is_symlink() or not source.is_file() or target.is_symlink():
                continue
            if not (staged.path / relative).is_dir() or (staged.path / relative).is_symlink():
                deployer.logger.warning(
                    f"{relative} is not in the new release, so its environment file is not carried"
                )
                continue
            fs.write_bytes(target, source.read_bytes(), mode=SECRET_MODE)
            copied.append(f"{relative}/{ENV_FILE_NAME}")
        if copied:
            deployer.logger.substep(f"Environment carried over: {', '.join(copied)}")
        return copied

    def _owns(self, unit: Service) -> bool:
        """
        Args:
            unit: A row of the services table.

        Returns:
            Whether it is one of this application's workspace units.
        """
        app = self.app()
        return app is not None and app.id is not None and unit.app_id == app.id

    def abandon(self) -> None:
        """
        Throw away a release that failed before it could serve.

        Recorded as failed and removed from disk, unless ``current`` points at it: whatever went
        wrong, the release that is serving is never the one deleted.
        """
        staged = self.staged
        if staged is None:
            return
        self.book().record_status(staged, ReleaseStatus.FAILED)
        discard_release(staged.path, releases=staged.manager, logger=self.deployer.logger)

    # -- the update -----------------------------------------------------------------------------

    def update(self, report: StepReporter) -> UpdateResult:
        """
        Build the latest source as a new release and switch every workspace to it.

        The order is the other release deployers': fetch, install, Prisma, build, permissions, the
        ``pre_deploy`` hooks, and only then the switch behind the health gate.

        Args:
            report: Called as each step begins.

        Returns:
            What was done.

        Raises:
            DeploymentError: A workspace did not answer on the new release; the one that served
                is active and restarted by then.
            NoustError: A step failed; the half-built release is removed.
        """
        from noust.deployers.interface import UpdateResult

        deployer = self.deployer
        report("Fetching into a new release")
        self.stage()
        if not deployer.workspaces:
            try:
                deployer._discover_workspaces()
            except DeploymentError as exc:
                # Only the build timeout estimate depends on the count, so a layout this cannot
                # read must not block a rebuild.
                deployer.logger.debug(f"Could not enumerate workspaces: {exc}")
        cancellation.checkpoint()
        report("Installing dependencies")
        deployer._install_dependencies()
        cancellation.checkpoint()
        report("Running database migrations")
        prisma_updated = deployer._run_prisma_migrations()
        cancellation.checkpoint()
        report("Building applications")
        deployer._build_all()
        cancellation.checkpoint()
        deployer._set_permissions()
        cancellation.checkpoint()
        deployer._run_pre_deploy_hooks(report)

        staged = self.require()
        report(f"Activating release {staged.id}")
        restarted = self.activate(staged)
        return UpdateResult(
            package_manager=deployer.package_manager,
            prisma_updated=prisma_updated,
            is_static=False,
            start_command="",
            restarted=restarted,
        )

    def activate(self, staged: StagedRelease) -> tuple[str, ...]:
        """
        Point ``current`` at a release, restart every workspace, and keep it only if all answer.

        A release that does not pass is recorded as failed and the previous one is activated and
        restarted again, so the monorepo goes back to what served a moment ago.

        Args:
            staged: The release.

        Returns:
            The units restarted.

        Raises:
            RolledBackError: A workspace did not answer; the previous release serves again.
            DeploymentError: A workspace did not answer and there is nothing to go back to, or
                the previous release does not answer either.
        """
        deployer = self.deployer
        units = deployer._units() or []
        # The point of no return of a cancellation: from here the release is switched in behind
        # the health gate, which finishes or puts back what served.
        cancellation.commit(f"activating release {staged.id}")
        previous = staged.manager.activate(staged.path)
        deployer.logger.substep(
            f"Activated release {staged.id}" + (f" (was {previous.id})" if previous else "")
        )
        outcome = deployer._restart_and_probe(units)
        if outcome.healthy:
            self.book().record_active(staged)
            self.book().prune(staged, self.keep())
            return outcome.restarted

        self.book().record_status(staged, ReleaseStatus.FAILED)
        if previous is None:
            raise DeploymentError(
                f"Release {staged.id} did not pass its health check", details=outcome.evidence
            )
        deployer.logger.warning(f"Release {staged.id} did not pass its health check")
        deployer.logger.substep(f"Going back to release {previous.id}")
        staged.manager.activate(previous.path)
        again: WorkspaceRestart = deployer._restart_and_probe(units)
        state = (
            "is active again" if again.healthy else "is active again but is not answering either"
        )
        error = RolledBackError if again.healthy else DeploymentError
        raise error(
            f"Release {staged.id} did not pass its health check; release {previous.id} {state}",
            details=outcome.evidence,
        )


def require_not_released(app: App | None, domain: str) -> None:
    """
    Refuse to deploy over a monorepo that is on the release layout.

    A deployment writes into the application's directory, which on releases holds ``releases/``,
    ``shared/`` and ``current``: it is updated instead.

    Args:
        app: The application's row, when it exists.
        domain: Its domain.

    Raises:
        DeploymentError: It is on releases.
    """
    if app is not None and app.layout == RELEASES:
        raise DeploymentError(
            f"{domain} is on the release layout; a deployment does not rebuild it in place",
            details=f"Update it: noust update {domain}. Going back is instant: "
            f"noust releases rollback {domain}.",
        )
