# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files of the repository that a guard over the whole tree reads: those git does not ignore.

A guard that walks ``docs/`` or the repository root meets things that are not part of the
product: a virtual environment, ``node_modules``, a build directory, working material kept out
of version control, a copy of the tree in a second checkout. All of them are already named by
the ignore rules of the repository (``.gitignore`` and the checkout's own ``info/exclude``), so
the guards ask those instead of keeping a list of their own that would say which directories
exist on one machine.

The rules are read the way git reads them for this purpose: a pattern with a slash in it is
anchored at the root, one without matches any name, a trailing slash means a directory, a leading
``!`` takes back what an earlier pattern ignored, and the last pattern that matches decides. A
file under an ignored directory is ignored. Only the parts of git's pattern language the
repository uses are understood (``*``, ``?`` and character classes; ``**`` is read as ``*``).
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable
from pathlib import Path


def _common_git_dir(root: Path) -> Path | None:
    """
    Find the directory that holds the checkout's shared ``info/exclude``.

    Args:
        root: The repository root.

    Returns:
        The git directory (the main one for a linked checkout), or None without git metadata.
    """
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if not dot_git.is_file():
        return None
    first = dot_git.read_text(encoding="utf-8", errors="replace").strip()
    if not first.startswith("gitdir:"):
        return None
    git_dir = Path(first.removeprefix("gitdir:").strip())
    common = git_dir / "commondir"
    if common.is_file():
        return (git_dir / common.read_text(encoding="utf-8").strip()).resolve()
    return git_dir


def ignore_patterns(root: Path) -> list[str]:
    """
    Read the ignore rules that apply to the tree.

    Args:
        root: The repository root.

    Returns:
        The patterns in the order git applies them: ``.gitignore`` first, then the checkout's own
        exclude file.
    """
    sources = [root / ".gitignore"]
    git_dir = _common_git_dir(root)
    if git_dir is not None:
        sources.append(git_dir / "info" / "exclude")
    patterns: list[str] = []
    for source in sources:
        if not source.is_file():
            continue
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def _matches(pattern: str, candidate: str, *, is_directory: bool) -> bool:
    """
    Tell whether one ignore pattern names a path.

    Args:
        pattern: The pattern without its leading ``!``.
        candidate: The path relative to the root, with ``/`` separators.
        is_directory: Whether the path is a directory.

    Returns:
        True when the pattern matches.
    """
    directories_only = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    if directories_only and not is_directory:
        return False
    if "/" in pattern:
        return fnmatch.fnmatchcase(candidate, pattern.lstrip("/"))
    return fnmatch.fnmatchcase(candidate.rsplit("/", 1)[-1], pattern)


def is_ignored(relative: str, patterns: Iterable[str], *, is_directory: bool = False) -> bool:
    """
    Tell whether git ignores a file or a directory.

    Args:
        relative: The path, relative to the root, with ``/`` separators.
        patterns: The rules, from :func:`ignore_patterns`.
        is_directory: Whether the path is a directory.

    Returns:
        True when the path, or a directory above it, is ignored.
    """
    rules = list(patterns)
    parts = relative.split("/")
    for depth in range(1, len(parts) + 1):
        candidate = "/".join(parts[:depth])
        ignored = False
        for rule in rules:
            negated = rule.startswith("!")
            leaf_is_directory = is_directory if depth == len(parts) else True
            if _matches(rule.removeprefix("!"), candidate, is_directory=leaf_is_directory):
                ignored = not negated
        if ignored:
            return True
    return False


def tree_files(
    root: Path,
    *,
    under: Iterable[str] | None = None,
    skip: Iterable[str] = (),
) -> list[Path]:
    """
    List the files of the tree that git does not ignore.

    Args:
        root: The repository root.
        under: Directories (relative to the root) to read; the whole tree when omitted.
        skip: Paths relative to the root that are not read even though they are tracked (a
            generated build), each a file or a directory.

    Returns:
        The files, sorted, as absolute paths.
    """
    patterns = ignore_patterns(root)
    skipped = tuple(item.rstrip("/") for item in skip)
    starts = [root / item for item in under] if under is not None else [root]
    found: list[Path] = []
    for start in starts:
        if not start.is_dir():
            continue
        for directory, subdirectories, names in os.walk(start):
            here = Path(directory)
            relative_here = here.relative_to(root).as_posix()
            prefix = "" if relative_here == "." else relative_here + "/"
            # Prune before descending: a virtual environment or a dependency tree is not walked.
            subdirectories[:] = sorted(
                d
                for d in subdirectories
                if d != ".git"
                and not is_ignored(prefix + d, patterns, is_directory=True)
                and (prefix + d) not in skipped
            )
            for name in sorted(names):
                relative = prefix + name
                if is_ignored(relative, patterns):
                    continue
                if any(relative == s or relative.startswith(s + "/") for s in skipped):
                    continue
                found.append(here / name)
    return found
