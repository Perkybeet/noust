# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which links of a checkout an update changed.

The guard of a compose stack warns, and never stops, about what the stack ran with already
(:mod:`noust.deployers.helpers.compose_verdict`). A link of the repository is part of what a
mount reaches, and an update that adds one, or turns a directory into one, changes where an
unchanged ``./data:/data`` goes. It is asked of git, which knows what the commits changed: a
link somebody made by hand in the tree is not in any commit, and was there before the update.

Kept beside :mod:`noust.managers.source_manager`, which does not grow, and asks git through it.
"""

from __future__ import annotations

from pathlib import Path

from noust.managers.source_manager import SourceManager, validate_commit_id

#: The mode git gives a symbolic link.
_LINK_MODE = "120000"


def links_changed(manager: SourceManager, repository: Path, commit: str) -> frozenset[Path] | None:
    """
    List the links the tree has on a path that differs between a commit and the one checked out.

    Args:
        manager: Runs git.
        repository: The checkout.
        commit: The commit the tree was on before the update.

    Returns:
        The absolute path of every link added, changed, or removed to make way for a directory
        (or the other way round) between ``commit`` and ``HEAD``; None when git cannot say, which
        the caller takes as every link having changed.
    """
    wanted = validate_commit_id(commit)
    result = manager._git(
        ["diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-abbrev", wanted, "HEAD"],
        cwd=repository,
        trust_tree=True,
    )
    if not result.success:
        return None
    tokens = result.stdout.split("\0")
    changed: set[Path] = set()
    for index in range(0, len(tokens) - 1, 2):
        modes = tokens[index].lstrip(":").split(" ")
        if len(modes) >= 2 and _LINK_MODE in modes[:2]:
            changed.add(repository / tokens[index + 1])
    return frozenset(changed)
