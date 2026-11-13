# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console server starts from anywhere (``python scripts/console_server.py``).

The launcher puts the repository on the path when it prepares its sandbox, which is after its own modules
have loaded. So a module of the launcher that imports ``tests.support`` at its top loads under pytest, where
the repository is already on the path, and fails with "No module named 'tests'" for the person who runs the
command the guide gives and for every worker of the console's end-to-end suite. That happened once, with the
machine that has Docker: every test passed and the command did not start.

What is defended: **no module of the launcher imports ``tests`` when it loads.** It imports it inside the
function that needs it, or under ``TYPE_CHECKING`` for a name it only writes in an annotation.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = [
    REPO / "scripts" / "console_server.py",
    *sorted((REPO / "scripts" / "console_server").glob("*.py")),
]


def _loaded_with_the_module(tree: ast.Module) -> list[ast.AST]:
    """
    Args:
        tree: A module.

    Returns:
        The statements that run when the module is imported: its own, and those of the blocks it runs then
        (``if``, ``try``, ``with``), but not the body of a function or a class's methods, and not what sits
        under ``if TYPE_CHECKING:``.
    """
    found: list[ast.AST] = []
    pending: list[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.If):
            test = node.test
            name = test.id if isinstance(test, ast.Name) else getattr(test, "attr", "")
            if name == "TYPE_CHECKING":
                pending.extend(node.orelse)
                continue
        found.append(node)
        for field in ("body", "orelse", "finalbody", "handlers"):
            pending.extend(
                child for child in getattr(node, field, []) if isinstance(child, ast.AST)
            )
    return found


def _imports_tests(node: ast.AST) -> bool:
    """
    Args:
        node: A statement.

    Returns:
        Whether it imports the ``tests`` package or something inside it.
    """
    if isinstance(node, ast.ImportFrom):
        return node.level == 0 and (node.module or "").split(".")[0] == "tests"
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "tests" for alias in node.names)
    return False


class TestTheLauncherStartsFromAnywhere:
    def test_the_launcher_is_where_this_test_looks(self) -> None:
        assert len(LAUNCHER) > 10, "the launcher's modules moved: this guard reads nothing"

    def test_no_module_of_the_launcher_imports_tests_when_it_loads(self) -> None:
        early = [
            f"{path.relative_to(REPO)}:{getattr(node, 'lineno', 0)}"
            for path in LAUNCHER
            for node in _loaded_with_the_module(ast.parse(path.read_text(encoding="utf-8")))
            if _imports_tests(node)
        ]
        assert early == [], (
            "the repository is not on the path yet when these load, so the command does not start outside "
            f"pytest; import inside the function that needs it: {early}"
        )
