# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the store remembers of an application's releases, written in one place.

The directories under ``releases/`` are the truth about what exists and which is active; the store
adds what a directory cannot say: when a release was made, whether it served, how it ended, and
the recent failures that were removed from disk. Every deployer that builds releases writes the
same rows at the same moments (staged, active, failed, pruned), and a row that cannot be written
is said and never fatal: the link on disk already decided.

:class:`ReleaseBook` is that writer. :class:`~noust.deployers.base.BaseDeployer` and the monorepo's
release layout (:mod:`noust.deployers.monorepo_releases`) both hold one, so the two cannot record
a release differently.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from noust.core.exceptions import NoustError
from noust.core.fs import DryRunFileSystem, FileSystem
from noust.core.logger import Logger
from noust.core.store import NoustStore, ReleaseRecord, ReleaseStatus
from noust.deployers.helpers.release_build import StagedRelease

__all__ = ["RECORDING_ERRORS", "ReleaseBook"]

#: Failures while writing release bookkeeping. The release on disk is the truth; a row that could
#: not be written is reported, never fatal.
RECORDING_ERRORS = (NoustError, sqlite3.Error)


class ReleaseBook:
    """
    Writes the rows of one application's releases.

    Args:
        store: The store.
        logger: Where a row that could not be written is said.
        fs: The filesystem the deployment runs through; under a rehearsal nothing is written.
        domain: The application.
    """

    def __init__(self, store: NoustStore, logger: Logger, fs: FileSystem, domain: str) -> None:
        self.store = store
        self.logger = logger
        self.fs = fs
        self.domain = domain

    def app_id(self) -> int | None:
        """
        Tell whether release rows can be written, and for which application.

        Returns:
            The application id, or None under ``--dry-run`` (the store rolls the app row back,
            so a release row would point at nothing) and when the application is not registered.
        """
        if isinstance(self.fs, DryRunFileSystem):
            return None
        app = self.store.get_app(self.domain) if self.domain else None
        return app.id if app is not None else None

    def record_row(self, staged: StagedRelease) -> None:
        """
        Remember a release that was just staged.

        Args:
            staged: The release.
        """
        app_id = self.app_id()
        if app_id is None:
            return
        try:
            if self.store.get_release(app_id, staged.id) is None:
                self.store.record_release(
                    ReleaseRecord(
                        id=staged.id,
                        app_id=app_id,
                        git_commit=staged.short_commit,
                        created_at=datetime.now(timezone.utc).isoformat(),
                        status=ReleaseStatus.BUILT.value,
                        path=str(staged.path),
                    )
                )
        except RECORDING_ERRORS as exc:
            self.logger.warning(f"Could not record release {staged.id}: {exc}")

    def record_status(self, staged: StagedRelease, status: ReleaseStatus) -> None:
        """
        Record how a release ended up.

        Args:
            staged: The release.
            status: Its new status.
        """
        app_id = self.app_id()
        if app_id is None:
            return
        try:
            self.store.set_release_status(app_id, staged.id, status.value)
        except RECORDING_ERRORS as exc:
            self.logger.warning(f"Could not record release {staged.id} as {status.value}: {exc}")

    def record_active(self, staged: StagedRelease) -> None:
        """
        Record that a release is now the active one.

        Args:
            staged: The release.
        """
        app_id = self.app_id()
        if app_id is None:
            return
        try:
            self.store.mark_release_active(app_id, staged.id)
        except RECORDING_ERRORS as exc:
            self.logger.warning(f"Could not record release {staged.id} as active: {exc}")

    def prune(self, staged: StagedRelease, keep: int) -> None:
        """
        Delete the releases beyond the application's retention, and forget them.

        Rows of releases that failed and were removed are kept while they are among the newest
        ``keep`` rows, so recent failures stay visible, and forgotten after that.

        Args:
            staged: The release just activated.
            keep: How many releases the application keeps on disk.
        """
        removed = staged.manager.prune(keep)
        for release in removed:
            self.logger.substep(f"Pruned release {release.id}")

        app_id = self.app_id()
        if app_id is None:
            return
        try:
            self.store.forget_pruned_releases(
                app_id,
                on_disk={release.id for release in staged.manager.list()},
                removed={release.id for release in removed},
                keep=keep,
            )
        except RECORDING_ERRORS as exc:
            self.logger.warning(f"Could not forget pruned releases: {exc}")
