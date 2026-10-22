# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files Noust keeps in an application's tree, and the names they had before 4.0.

Four things live next to the application's code and are Noust's own: the inventory of its
variables, the stamp of the runtime that installed each dependency directory, the PHP settings
and the directory PHP uploads go through. Until 4.0 they were called after the old product.
An application deployed before keeps them under that name until its next deployment (or
``noust setup rename``) moves them; in the meantime they are read under either name, and the
new one wins when both exist.

A tree is a repository, and a repository is untrusted input: a link under either name
(a link to ``/etc`` in a commit, under either name) is never followed, read or renamed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from noust.core import legacy_names
from noust.core.fs import FileSystem

__all__ = [
    "DEPENDENCY_DIRS",
    "ENV_DIR",
    "PHP_SETTINGS",
    "PHP_TMP",
    "RUNTIME_STAMP",
    "adopt",
    "legacy_of",
    "pending",
    "present",
    "sweep",
]

_logger = logging.getLogger(__name__)

#: The inventory of an application's variables, in a directory only Noust reads.
ENV_DIR = ".noust"
#: The runtime that installed a dependency directory, inside it.
RUNTIME_STAMP = ".noust-runtime.json"
#: The PHP settings of an application.
PHP_SETTINGS = ".noust-php.json"
#: The directory PHP uploads go through, beside the applications.
PHP_TMP = ".noust-php-tmp"

_LEGACY = {
    ENV_DIR: legacy_names.TREE_ENV_DIR,
    RUNTIME_STAMP: legacy_names.TREE_RUNTIME_STAMP,
    PHP_SETTINGS: legacy_names.TREE_PHP_SETTINGS,
    PHP_TMP: legacy_names.TREE_PHP_TMP,
}

#: The directories an install fills with dependencies, which hold a runtime stamp each.
DEPENDENCY_DIRS = ("node_modules", ".venv", "venv")


def legacy_of(name: str) -> str:
    """
    Give the name a file had before 4.0.

    Args:
        name: One of this module's names.

    Returns:
        The old spelling.

    Raises:
        KeyError: When the name is not one of Noust's tree files.
    """
    return _LEGACY[name]


def present(directory: Path, name: str) -> Path | None:
    """
    Find a tree file under whichever name it has.

    Args:
        directory: Where it lives.
        name: One of this module's names.

    Returns:
        The new name's path when it exists, else the old name's, else None. A link is not found:
        nothing here is read through one.
    """
    for candidate in (directory / name, directory / _LEGACY[name]):
        if candidate.is_symlink():
            continue
        if candidate.exists():
            return candidate
    return None


def adopt(directory: Path, name: str, *, fs: FileSystem) -> bool:
    """
    Give a tree file its current name.

    Args:
        directory: Where it lives.
        name: One of this module's names.
        fs: The filesystem the change goes through.

    Returns:
        True when something changed on disk. When both names exist the new one is kept and the
        old one removed; a link under the old name is left where it is.
    """
    old, new = directory / _LEGACY[name], directory / name
    if old.is_symlink() or not old.exists():
        return False
    try:
        if new.is_symlink():
            return False
        if new.exists():
            if old.is_dir():
                fs.remove_tree(old)
            else:
                fs.remove(old)
        else:
            fs.rename(old, new)
    except OSError as exc:
        _logger.warning("Could not rename %s to %s: %s", old, new, exc)
        return False
    return True


def _places(app_path: Path) -> list[tuple[Path, str]]:
    """
    List where Noust keeps its files in one application's tree.

    Args:
        app_path: The application's directory.

    Returns:
        Each directory and the name of the file or directory in it that is Noust's.
    """
    if app_path.is_symlink() or not app_path.is_dir():
        return []
    places: list[tuple[Path, str]] = [
        (app_path, ENV_DIR),
        (app_path, PHP_SETTINGS),
        (app_path / "shared", ENV_DIR),
        (app_path / "shared", PHP_SETTINGS),
    ]
    releases = app_path / "releases"
    if releases.is_dir() and not releases.is_symlink():
        for release in sorted(releases.iterdir()):
            if release.is_symlink() or not release.is_dir():
                continue
            places.append((release, ENV_DIR))
            places.extend((release / dependency, RUNTIME_STAMP) for dependency in DEPENDENCY_DIRS)
    for dependency in DEPENDENCY_DIRS:
        places.append((app_path / dependency, RUNTIME_STAMP))
    return [
        (directory, name)
        for directory, name in places
        if directory.is_dir() and not directory.is_symlink()
    ]


def pending(app_path: Path) -> list[Path]:
    """
    List the files of one application's tree that still have a name from before 4.0.

    Args:
        app_path: The application's directory.

    Returns:
        The old paths, links excluded; empty when the tree is current.
    """
    return [
        directory / legacy_of(name)
        for directory, name in _places(app_path)
        if (directory / legacy_of(name)).exists() and not (directory / legacy_of(name)).is_symlink()
    ]


def sweep(app_path: Path, *, fs: FileSystem) -> list[Path]:
    """
    Move every old name in one application's tree to the current one.

    Looks where Noust writes them: the application's root and its ``shared`` directory, and the
    dependency directories of every release it keeps.

    Args:
        app_path: The application's directory.
        fs: The filesystem the changes go through.

    Returns:
        The paths now in place of an old name; empty when nothing was left.
    """
    changed: list[Path] = []
    for directory, name in _places(app_path):
        if adopt(directory, name, fs=fs):
            changed.append(directory / name)
    return changed
