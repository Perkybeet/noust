# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The names an installed server reads.

A file move inside the package is safe for the code in the tree and unsafe for
the machines already running: a systemd unit or a timer written by an earlier
version names a module, and an upgrade does not rewrite it. When the module
moves, that unit stops starting and nothing in the test suite says so, because
every test imports the module by its new name.

These are the names a server holds that are not in the source of the version it
upgrades to. Moving one means leaving a module of one line under the old name
(as ``noust.main`` does) and writing down until when in ``core/legacy_names.py``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

import noust
from noust.managers import network_maintenance
from noust.managers.server import security_checks, security_pending, updates_unit

REPO = Path(noust.__file__).resolve().parent.parent.parent
SRC = REPO / "src"

#: The modules a transient unit runs with ``python -m``. They are in the unit's
#: command line, which the server keeps until the unit ends and, for the timer
#: that undoes an access change, across an upgrade.
MODULES_RUN_WITH_DASH_M = (
    "noust.managers.network_maintenance",
    "noust.managers.server.security_checks",
    "noust.managers.server.security_pending",
)


def _toml(path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - only on 3.10
        import tomli as tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def _source_of(module: str) -> Path:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None, f"{module} does not resolve"
    return Path(spec.origin)


def _runs_main_when_executed(source: str) -> bool:
    """Whether the module has an ``if __name__ == "__main__":`` block."""
    for node in ast.parse(source).body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        ):
            return True
    return False


class TestTheConsoleScript:
    """``noust`` on the path of an installed server is a stub made by pip or the package."""

    def test_pyproject_declares_the_entry_point_current_installs_get(self):
        scripts = _toml(REPO / "pyproject.toml")["project"]["scripts"]
        assert scripts == {"noust": "noust.cli.app:entrypoint"}

    def test_the_entry_point_resolves_to_a_callable(self):
        module, _, attribute = "noust.cli.app:entrypoint".partition(":")
        assert callable(getattr(importlib.import_module(module), attribute))

    def test_noust_main_still_exposes_cli_and_main(self):
        """
        ``noust.main:cli`` is the console script every copy installed before
        the command tree moved still carries; the upgrade does not rewrite it.
        """
        main = importlib.import_module("noust.main")

        assert callable(main.cli)
        assert callable(main.main)
        assert set(main.__all__) == {"cli", "main"}

    def test_noust_main_cli_is_the_current_entry_point(self):
        """A second copy of the entry point would drift from the first."""
        from noust.cli.app import entrypoint
        from noust.main import cli

        assert cli is entrypoint

    def test_the_module_is_runnable(self):
        """``python -m noust`` is what ``updates_unit`` and the fleet's tunnels run."""
        assert importlib.util.find_spec("noust.__main__") is not None
        source = _source_of("noust.__main__").read_text(encoding="utf-8")

        assert "from noust.main import cli" in source
        assert _runs_main_when_executed(source)

    def test_the_update_unit_runs_the_package_as_a_module(self):
        assert updates_unit.noust_command() == [sys.executable, "-m", "noust"]


class TestTheModulesATransientUnitRuns:
    """What ``systemd-run ... python -m <module>`` names, and that the module answers to it."""

    @pytest.mark.parametrize("module", MODULES_RUN_WITH_DASH_M)
    def test_the_module_resolves(self, module: str):
        assert importlib.util.find_spec(module) is not None

    @pytest.mark.parametrize("module", MODULES_RUN_WITH_DASH_M)
    def test_the_module_runs_main_when_executed(self, module: str):
        """``python -m`` imports the module as ``__main__``: without the block it does nothing."""
        assert _runs_main_when_executed(_source_of(module).read_text(encoding="utf-8"))

    def test_network_maintenance_names_itself_in_its_command(self):
        argv = network_maintenance.refresh_command(python="/usr/bin/python3")

        assert network_maintenance.MODULE == "noust.managers.network_maintenance"
        assert argv[argv.index("--") + 1 :] == [
            "/usr/bin/python3",
            "-m",
            "noust.managers.network_maintenance",
        ]

    def test_security_checks_names_itself_in_its_command(self):
        argv = security_checks.refresh_command(python="/usr/bin/python3")

        assert security_checks.MODULE == "noust.managers.server.security_checks"
        assert argv[argv.index("--") + 1 :] == [
            "/usr/bin/python3",
            "-m",
            "noust.managers.server.security_checks",
            "refresh",
        ]

    def test_security_pending_names_itself_in_the_timer_that_undoes_a_change(self, tmp_path: Path):
        """
        This one is spelt ``__name__`` in the source, so the name is whatever
        the file's path makes it: moving the file changes what an armed timer
        runs after the upgrade. The ``prog`` line the parser prints says it too.
        """
        ledger = security_pending.ChangeLedger(tmp_path, python="/usr/bin/python3")
        change = security_pending.PendingChange(
            id="abc123",
            kind="sshd",
            title="Change the port",
            actor="test",
            applied_at=1000.0,
            expires_at=1060.0,
        )

        argv = ledger.timer_argv(change)

        after = argv[argv.index("--") + 1 :]
        assert after[:3] == ["/usr/bin/python3", "-m", "noust.managers.server.security_pending"]
        assert after[3] == "revert"
        source = _source_of("noust.managers.server.security_pending").read_text(encoding="utf-8")
        assert 'prog="python -m noust.managers.server.security_pending"' in source
