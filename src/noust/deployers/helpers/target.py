# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The directory a deploy writes into, and whether it may.

``noust create`` over a domain whose directory already existed used to destroy
it. The in-place fetch empties its target before cloning, so an application
deployed in place lost its ``.env`` and every file it had written; a Docker
Compose project lost the data its services bind-mount from the tree. And a
deploy that failed afterwards ran its undo, which deleted the whole directory,
including when Noust had no record of it because the store had moved.

Every deployer asks :func:`claim_deploy_target` before it fetches anything.
A directory that is missing or empty is the deploy's to fill. One that holds
files is refused with the way forward, unless the deploy only adds to it (an
application already on releases gets a new release beside the ones it has)
or the operator asked to deploy over it (then :func:`fetch_into_target` brings a
git checkout up to date in place and refuses anything else, never emptying
it), or the deploy is an adoption that
registers a running stack where it is and fetches nothing. Either way the
answer records whether
the directory was there before, so a failed deploy never removes a directory
it did not create.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem
from noust.core.logger import Logger
from noust.core.store import App
from noust.deployers.helpers.layout import RELEASES

if TYPE_CHECKING:
    from noust.managers.source_manager import SourceManager


@dataclass(frozen=True)
class DeployTarget:
    """
    The application directory, as a deploy found it.

    Attributes:
        path: The directory.
        existed: It was there before the deploy.
        had_files: It held something before the deploy.
    """

    path: Path
    existed: bool
    had_files: bool

    @property
    def created_here(self) -> bool:
        """Whether this deploy created the directory, and may remove it on failure."""
        return not self.existed

    def undo_fetch(self, fs: FileSystem, logger: Logger) -> None:
        """
        Take back what a failed deploy fetched into the directory, and nothing more.

        A directory this deploy created is removed. One that was there and
        empty (a mount point, a directory made ahead of time) is emptied and
        kept. One that held files is left exactly as it is: they were not
        this deploy's to delete.

        Args:
            fs: The filesystem the removal goes through.
            logger: Where what was kept is reported.
        """
        if not self.path.exists():
            return
        try:
            if self.created_here:
                fs.remove_tree(self.path)
            elif not self.had_files:
                for entry in sorted(self.path.iterdir()):
                    if entry.is_dir() and not entry.is_symlink():
                        fs.remove_tree(entry)
                    else:
                        fs.remove(entry)
            else:
                logger.warning(
                    f"{self.path} held files before this deploy; it is left as it is now"
                )
        except OSError as error:
            # An undo runs after something already failed; a directory that
            # will not go away must not hide that failure.
            logger.warning(f"Could not clean up {self.path}: {error}")


def claim_deploy_target(
    path: Path,
    *,
    domain: str,
    existing: App | None,
    replace: bool,
    adopt: bool = False,
) -> DeployTarget:
    """
    Decide whether a deploy may write into an application directory.

    Args:
        path: The application directory.
        domain: The domain being deployed.
        existing: The application's store row, when Noust has one.
        replace: The operator asked to deploy over whatever is there
            (``noust create --force``).
        adopt: The deploy registers what is already there and fetches
            nothing into it (``noust app adopt``): the files are the
            application, so they are accepted as they are, and never removed.

    Returns:
        How the directory was found.

    Raises:
        DeploymentError: It holds files and the deploy would replace them,
            and replacing was not asked for.
    """
    existed = os.path.lexists(path)
    if existed and (path.is_symlink() or not path.is_dir()):
        raise DeploymentError(
            f"{path} is not a directory",
            details="Noust deploys into a real directory. Move what is there aside and retry.",
        )
    had_files = existed and any(path.iterdir())
    target = DeployTarget(path=path, existed=existed, had_files=had_files)
    if not had_files or replace or adopt:
        return target
    # A redeploy of an application on releases builds one more release next
    # to the ones it has; its shared/ and the release serving stay as they are.
    if existing is not None and existing.layout == RELEASES:
        return target

    if existing is not None:
        raise DeploymentError(
            f"{domain} is already deployed in place at {path}",
            details=f"To bring it up to date, run: noust update {domain}. To deploy over it "
            f"anyway, back it up first (noust backup create {domain}) and deploy again with "
            "--force: a git checkout is brought up to date in place, keeping what git does not "
            "track; a directory that is not one is never emptied.",
        )
    raise DeploymentError(
        f"{path} already exists and is not empty",
        details=f"Noust has no record of an application in it. If one was deployed there, the "
        "store may have moved: compare 'noust store path' with where it used to be before "
        f"deploying anything. Otherwise move {path} aside, or deploy with --force to deploy "
        "over it: in place a git checkout is brought up to date, keeping what git does not "
        "track (a directory that is not a checkout is never emptied); on releases a release "
        "is added beside them.",
    )


def fetch_into_target(
    target: DeployTarget,
    source_manager: SourceManager,
    source: str,
    *,
    branch: str | None,
    domain: str,
    logger: Logger,
) -> None:
    """
    Fetch a deploy's source into its directory, never emptying one that holds files.

    The one in-place fetch of a deploy, whichever deployer it is (rule 4): a
    missing or empty directory is cloned into; one that held files, which
    only ``--force`` lets a deploy into, is brought up to date in place when
    it is a git checkout (a reset that keeps every untracked file: the
    ``.env``, uploads, a stack's bind-mounted data) and refused otherwise.
    The clean fetch used to run before the type was even known, so ``noust
    create --force`` without ``-t`` emptied a Compose project's data.

    Args:
        target: The directory, as :func:`claim_deploy_target` found it.
        source_manager: What fetches.
        source: The source to fetch.
        branch: The branch, if one was named.
        domain: The domain being deployed, for the way forward.
        logger: Where what was kept is reported.

    Raises:
        DeploymentError: The directory holds files and is not a git checkout.
        SourceError: The fetch failed; nothing was removed.
    """
    if not target.had_files:
        source_manager.fetch(source=source, destination=target.path, branch=branch)
        return
    if not (target.path / ".git").exists():
        raise DeploymentError(
            f"{target.path} holds files and is not a git checkout",
            details="Noust does not empty a directory that holds files to deploy into it: an "
            "application keeps its .env and its data beside its code. Move what is there "
            f"aside (or back it up: noust backup create {domain}) and deploy again.",
        )
    source_manager.fetch(
        source=source, destination=target.path, branch=branch, clean=False, force=True
    )
    logger.substep(
        f"Checkout at {target.path} brought up to date in place; files git does not "
        "track (the .env, bind-mounted data) are kept"
    )
