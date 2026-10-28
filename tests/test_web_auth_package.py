# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust.web.auth`` is a package that was one module of six thousand lines.

What has to hold of that: every name the module had is still there, the log records still carry
the module's old name, and the clock the tests move is read from the modules the test helper
moves it in.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path
from types import ModuleType

import pytest

from noust.web import auth
from tests.support.auth_clock_support import CLOCK_READERS

PACKAGE_DIR = Path(auth.__file__).parent


def submodules() -> list[ModuleType]:
    return [
        importlib.import_module(f"{auth.__name__}.{info.name}")
        for info in pkgutil.iter_modules([str(PACKAGE_DIR)])
    ]


class TestTheNamesTheModuleHad:
    def test_every_name_the_package_lists_is_there(self) -> None:
        missing = [name for name in auth.__all__ if not hasattr(auth, name)]

        assert missing == []

    def test_a_name_is_the_object_the_module_that_defines_it_holds(self) -> None:
        by_module = {module.__name__: module for module in submodules()}
        different = [
            name
            for name in auth.__all__
            if name != "AuditLogger"
            and not any(
                getattr(module, name, object()) is getattr(auth, name)
                for module in by_module.values()
            )
        ]

        assert different == []

    def test_a_private_name_is_not_re_exported(self) -> None:
        """A test that patches one then fails loudly instead of patching a copy nobody reads."""
        assert [name for name in auth.__all__ if name.startswith("_")] == []
        assert not hasattr(auth, "_now")
        assert not hasattr(auth, "_global_token_manager")


class TestLogRecords:
    def test_every_module_logs_under_the_name_the_module_had(self) -> None:
        loggers = {
            module.__name__: module.logger.name
            for module in submodules()
            if hasattr(module, "logger")
        }

        assert loggers
        assert set(loggers.values()) == {"noust.web.auth"}


class TestTheClock:
    def test_the_helper_moves_the_clock_in_every_module_that_reads_it(self) -> None:
        readers = set()
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module == f"{auth.__name__}.clock":
                    if any(alias.name == "_now" for alias in node.names):
                        readers.add(f"{auth.__name__}.{path.stem}")

        assert readers == {module.__name__ for module in CLOCK_READERS}

    def test_moving_it_moves_what_a_session_and_a_window_of_elevation_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.auth import payload
        from tests.support.auth_clock_support import set_clock

        set_clock(monkeypatch, lambda: 1_000.0)

        assert payload.is_elevated({"elevated_until": 1_001.0}) is True
        set_clock(monkeypatch, lambda: 1_002.0)
        assert payload.is_elevated({"elevated_until": 1_001.0}) is False
