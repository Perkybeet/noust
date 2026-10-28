# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_json.py`` that other tests build on."""

from __future__ import annotations

import click

from noust.cli.app import cli as root_cli
from tests.support.cli_tree_support import walk


def _leaf_commands() -> list[tuple[str, click.Command]]:
    """
    Walk the real command tree and collect every leaf (non-group) command.

    Every module is imported through :class:`~noust.cli.app.LazyGroup`, exactly
    as a real invocation would, so this sees the tree as it is actually built,
    aliases resolved to one canonical entry each.

    Returns:
        ``(space-separated path, command)`` pairs, in a stable order.
    """
    return [
        (" ".join(path), command)
        for path, command in walk(root_cli)
        if not isinstance(command, click.Group)
    ]


LEAF_COMMANDS = _leaf_commands()
