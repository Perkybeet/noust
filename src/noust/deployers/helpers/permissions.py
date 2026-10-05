# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ownership hand-over for deployed trees.

The whole deployment runs as root, but the generated unit runs as the
configured service user. That user must be able to write into the app
directory: pnpm creates a temporary file in the project root on every
``pnpm run``, and Next.js writes into ``.next`` at runtime, so a root-owned
tree fails at start with EACCES and systemd restarts it forever.

This is the one implementation both the base pipeline and the monorepo
deployer use; it used to exist only in the monorepo deployer, which is why
every other app type shipped with the bug above.
"""

from __future__ import annotations

import errno
from collections.abc import Iterable
from pathlib import Path

from noust.core.fs import SECRET_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.runner import CommandRunner

#: Deadline for the recursive chown and chmod over a deployed tree.
_PERMISSIONS_TIMEOUT = 60


def hand_over_tree(
    app_path: Path,
    *,
    user: str,
    group: str,
    runner: CommandRunner,
    fs: FileSystem,
    logger: Logger,
    env_files: Iterable[Path] = (),
) -> None:
    """
    Make ``user`` the owner of the deployed tree, keeping secrets private.

    Args:
        app_path: Root of the deployed application.
        user: Account the service runs as.
        group: Group the service runs as.
        runner: Runner the chown and chmod execute through.
        fs: Filesystem used to restore the mode of the secret files.
        logger: Logger for the non-fatal failures.
        env_files: Files holding secrets whose mode must come back to 0600
            after the recursive chmod.
    """
    result = runner.run(
        ["chown", "-R", f"{user}:{group}", str(app_path)],
        timeout=_PERMISSIONS_TIMEOUT,
    )
    # Not fatal: the build is good, and an app that never writes runs fine. One
    # that does fails with EACCES in its own log, far from here, so this is the
    # only place the operator can be told the cause.
    if not result.success:
        logger.warning(
            f"Could not hand {app_path} over to {user}:{group}; the service may fail "
            f"with EACCES: {result.stderr.strip()}"
        )

    # Directories need the execute bit and the built assets must stay readable
    # by the web server, whatever umask the fetch and the build left behind.
    result = runner.run(
        ["chmod", "-R", "u+rwX,g+rX,o+rX", str(app_path)],
        timeout=_PERMISSIONS_TIMEOUT,
    )
    if not result.success:
        logger.warning(
            f"Could not make {app_path} readable by the web server: {result.stderr.strip()}"
        )

    # That -R also put o+r on the .env files, which hold the secrets this
    # deployment was given. The chown above has just made the service account
    # their owner, so 0600 is readable by the application and by nobody else.
    # The tree is a repository's, so a name here can be a link it committed:
    # neither the entry nor a directory of the tree above it is followed out
    # of the tree. A file named outside the tree (``shared/.env`` handed over
    # with a release) is Noust's own, and only its leaf is checked.
    for env_file in env_files:
        if env_file.is_symlink() or escapes(env_file, app_path):
            logger.warning(
                f"Left {env_file} alone: it is a symbolic link, or under one, that "
                "leads out of the application; the repository should not commit it"
            )
            continue
        if env_file.is_file():
            fs.chmod(env_file, SECRET_MODE, follow_symlinks=False)


def escapes(path: Path, root: Path) -> bool:
    """
    Say whether a path named inside a tree leads out of it through a link.

    Args:
        path: The path as named.
        root: The tree.

    Returns:
        True when ``path`` is named under ``root`` but a directory link on
        the way takes it elsewhere. A path named outside ``root`` is not the
        tree's to judge, and is False.
    """
    return path.is_relative_to(root) and not is_inside(path, root)


def is_inside(path: Path, root: Path) -> bool:
    """
    Say whether a path stays inside a tree once every link on the way is followed.

    Args:
        path: A path under ``root`` as named, which may not exist yet.
        root: The tree.

    Returns:
        True when the directory holding ``path``, with every link on the way
        resolved, is ``root`` or under it. The leaf itself is not resolved:
        whether it may be a link is the caller's rule.
    """
    return path.parent.resolve().is_relative_to(root.resolve())


def hand_over_file(
    path: Path,
    *,
    user: str,
    group: str,
    mode: int,
    logger: Logger,
    fs: FileSystem | None = None,
) -> bool:
    """
    Make ``user:group`` the owner of a single file or directory and set its mode.

    Unlike :func:`hand_over_tree`, a failure here is not something the caller
    can shrug off: a restore that goes on to report "restored" over a file
    still owned by root, or a database engine that reports success while its
    own account cannot read the snapshot it was just handed, is the silent
    failure CONTRIBUTING.md rule 2 exists to remove. So this returns whether it
    actually worked instead of only logging a warning.

    The change goes to the inode the path names when it is pinned, never to
    the path by name: these files live in directories other accounts own (an
    engine's data directory, an application's tree), and a ``chown`` by name
    after a check follows a link swapped in between the two, handing the
    file it points at - ``/etc/shadow`` - to that account.

    Args:
        path: File or directory to hand over.
        user: Account that must own it.
        group: Group that must own it.
        mode: Permission bits to apply; setuid, setgid and sticky are dropped.
        logger: Logger for the failure, when there is one.
        fs: Filesystem the change goes through; the process-wide one by default.

    Returns:
        True if the owner and the mode were both applied (or, in a rehearsal,
        would have been).
    """
    filesystem = fs or get_fs()
    try:
        filesystem.set_owner(path, user=user, group=group, mode=mode)
    except KeyError:
        logger.warning(f"Could not hand {path} over to {user}:{group}: no such account or group")
        return False
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            logger.warning(f"Refusing to hand {path} over: it is a symbolic link")
        else:
            logger.warning(f"Could not hand {path} over to {user}:{group}: {exc}")
        return False
    return True
