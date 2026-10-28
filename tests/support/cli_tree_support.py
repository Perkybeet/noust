# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one way the tests that judge the whole command tree walk it.

Walking with ``list_commands`` and ``get_command`` misses what the root dispatches by itself: the
verbs of ``noust deploy``. :func:`noust.cli.app.documented_commands` is the reader that knows
them, so every guard that visits "every command" goes through here and none keeps a walker of its
own.
"""

from __future__ import annotations

from collections.abc import Iterator

import click

from noust.cli.app import documented_commands


def walk(
    command: click.Command,
    path: tuple[str, ...] = (),
    parent: click.Context | None = None,
) -> Iterator[tuple[tuple[str, ...], click.Command]]:
    """
    Visit a command and everything under it, hidden commands included.

    Args:
        command: Where to start, normally the root.
        path: Names leading to it.
        parent: The context of the group above, so a group that reads its parent sees it.

    Yields:
        ``(path, command)`` for the command and then each descendant, by name.
    """
    yield path, command
    if isinstance(command, click.Group):
        ctx = click.Context(command, parent=parent, info_name=command.name or "cli")
        for name, child in documented_commands(command, ctx):
            yield from walk(child, (*path, name), ctx)
