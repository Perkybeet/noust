# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the suite keeps what its tests share.

A test module that other tests import cannot be renamed, moved or deleted without taking those
tests with it, and importing it runs its tests again wherever a ``Test`` class came along. What
tests share lives in ``tests/support``, as modules that are not tests; ``conftest.py`` holds the
fixtures every test may ask for. These guards keep it that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


def imported_test_modules(source: str, importer: Path) -> list[str]:
    """
    The test modules a source imports.

    Args:
        source: The text of a Python file.
        importer: Where the file is, to resolve a relative import and to find the module a name
            imported from a package stands for.

    Returns:
        The dotted names of the test modules it imports, in the order it imports them.
    """
    package = list(importer.relative_to(ROOT).with_suffix("").parts[:-1])
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith("tests.") and alias.name.split(".")[-1].startswith("test_")
            )
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            if module.split(".")[0] != "tests":
                continue
            if module.split(".")[-1].startswith("test_"):
                found.append(module)
                continue
            for alias in node.names:
                candidate = ROOT.joinpath(*module.split("."), alias.name).with_suffix(".py")
                if alias.name.startswith("test_") and candidate.is_file():
                    found.append(f"{module}.{alias.name}")
    return found


def python_files() -> list[Path]:
    """Every Python file of the suite, the integration harness and the support modules included."""
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_test_module_imports_another() -> None:
    offenders = [
        f"{path.relative_to(ROOT)} imports {name}"
        for path in python_files()
        for name in imported_test_modules(path.read_text(encoding="utf-8"), path)
    ]

    assert not offenders, (
        "A test module is not a library. Move what both use to tests/support and import it "
        "from there:\n  " + "\n  ".join(offenders)
    )


def test_what_is_not_a_test_is_not_loose_in_the_tests_directory() -> None:
    allowed = {"__init__.py", "conftest.py"}
    loose = [
        p.name
        for p in sorted(TESTS.glob("*.py"))
        if not p.name.startswith("test_") and p.name not in allowed
    ]

    assert not loose, f"Put these in tests/support: {loose}"


class TestTheDetector:
    """The guard above reads imports; these are the spellings it has to see and the ones it spares."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("from tests.test_web_auth import make_config", ["tests.test_web_auth"]),
            ("import tests.test_web_auth", ["tests.test_web_auth"]),
            ("from tests.recipes.test_api import small_app", ["tests.recipes.test_api"]),
            ("from .test_web_auth import make_config", ["tests.test_web_auth"]),
            ("from tests import test_health_check", ["tests.test_health_check"]),
            ("def f():\n    from tests.test_web_auth import iter_routes", ["tests.test_web_auth"]),
        ],
    )
    def test_it_sees_every_way_to_import_a_test_module(
        self, source: str, expected: list[str]
    ) -> None:
        assert imported_test_modules(source, TESTS / "test_example.py") == expected

    @pytest.mark.parametrize(
        "source",
        [
            "from tests.support.web_auth import make_config",
            "from tests.conftest import PortProbe",
            "from tests import support",
            "from tests.support import web_auth",
            "import tests.support.web_auth",
            "from noust.core.test_helpers import thing",
        ],
    )
    def test_it_spares_the_support_modules_and_the_conftest(self, source: str) -> None:
        assert imported_test_modules(source, TESTS / "test_example.py") == []
