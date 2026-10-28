# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every walker of the command tree sees the verbs of ``noust deploy``.

The root lists no ``deploy`` (it is ``noust create`` under its old name) and hands the verbs of
``DEPLOY_VERBS`` to their own group by itself. A walker that asked the root only for what it lists
never reached them, so the command-surface script and the audit, JSON and flag guards passed over
``deploy cancel``, ``deploy freeze`` and the rest without ever looking at them. The generated
reference had the same hole and was closed in :func:`noust.cli.app.documented_commands`; these
guards now read the tree through it.
"""

from __future__ import annotations

import pytest

from noust.cli.app import DEPLOY_VERBS, cli
from scripts import cli_parity
from tests import test_cli_audit, test_cli_json, test_cli_surface
from tests.support.cli_tree_support import walk

#: The eleven verbs and the five commands of ``deploy schedule``: sixteen things to type.
DEPLOY_PATHS = {f"deploy {verb}" for verb in DEPLOY_VERBS} | {
    f"deploy schedule {name}" for name in ("list", "remove", "run", "set", "show")
}


def test_there_are_commands_to_see() -> None:
    """The guards below are not vacuous."""
    assert len(DEPLOY_PATHS) == 16


def test_the_shared_walker_reaches_them() -> None:
    paths = {" ".join(path) for path, _command in walk(cli)}
    assert DEPLOY_PATHS <= paths


def test_the_command_surface_script_reaches_them() -> None:
    assert DEPLOY_PATHS <= set(cli_parity.click_tree(cli))


def test_the_audit_guards_reach_them() -> None:
    assert test_cli_audit.walk is walk


def test_the_json_guards_reach_them() -> None:
    # A leaf is a command that runs; `deploy schedule` is the group that holds five of them.
    paths = {path for path, _command in test_cli_json.LEAF_COMMANDS}
    assert DEPLOY_PATHS - {"deploy schedule"} <= paths


def test_the_global_flag_guard_reaches_them() -> None:
    assert test_cli_surface.walk is walk


@pytest.mark.parametrize("path", sorted(DEPLOY_PATHS - {"deploy schedule"}))
def test_a_command_walks_as_itself(path: str) -> None:
    """The guards judge the commands the verbs hold, not only the group that holds them."""
    found = {" ".join(names): command for names, command in walk(cli)}
    assert found[path].name == path.rsplit(" ", 1)[-1]
