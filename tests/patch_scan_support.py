# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the tests replace an attribute of a module, found in the syntax tree.

A module that becomes a package keeps every name it had by importing it in its ``__init__``,
and a test that replaces one of those names on the package changes what the test reads and not
what the code under test looks up: the code reads the name in the submodule that defines it.
That does not fail, it makes the test stop testing, so ``tests/test_patch_targets.py`` looks for it.
This reads the tests; it never imports them.

Three forms replace an attribute: a string path (``monkeypatch.setattr("pkg.mod.name", ...)``,
``mock.patch("pkg.mod.name")``), the module and the name (``monkeypatch.setattr(mod, "name", ...)``
where ``mod`` is the module imported in that file) and an assignment (``mod.name = ...``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: The calls whose first argument is what gets patched and, when it is a module, whose second is the
#: name.
PATCH_CALLS = frozenset({"setattr", "delattr", "setitem", "object", "patch", "dict", "multiple"})


@dataclass(frozen=True)
class PatchSite:
    """
    One place a test replaces an attribute of a module.

    Attributes:
        path: The test file, relative to the repository root.
        line: The line of the call or the assignment.
        name: The attribute, as written after the module's name (``time.time`` for a patch of the
            ``time`` module's function reached through the module).
    """

    path: str
    line: int
    name: str


def _dotted(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _aliases(tree: ast.AST, module: str) -> set[str]:
    parent, _, leaf = module.rpartition(".")
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(a.asname for a in node.names if a.name == module and a.asname)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == parent:
            aliases.update(a.asname or a.name for a in node.names if a.name == leaf)
    return aliases


def patches_of(tree: ast.AST, module: str, path: str) -> list[PatchSite]:
    """
    Find where one file replaces an attribute of a module.

    Args:
        tree: The file's syntax tree.
        module: The dotted module name.
        path: The file, for the report.

    Returns:
        Each replacement, in the order they appear.
    """
    aliases = _aliases(tree, module)
    found: list[PatchSite] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            function = node.func
            called = function.attr if isinstance(function, ast.Attribute) else None
            called = called or getattr(function, "id", None)
            if called not in PATCH_CALLS:
                continue
            first = node.args[0]
            second = node.args[1] if len(node.args) > 1 else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value.startswith(module + "."):
                    found.append(PatchSite(path, node.lineno, first.value[len(module) + 1 :]))
            elif _dotted(first) in aliases | {module}:
                if isinstance(second, ast.Constant) and isinstance(second.value, str):
                    found.append(PatchSite(path, node.lineno, second.value))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and _dotted(target.value) in aliases | {
                    module
                }:
                    found.append(PatchSite(path, node.lineno, target.attr))
    return found


def patches_in_tree(root: Path, directories: tuple[str, ...], module: str) -> list[PatchSite]:
    """
    Find every replacement of an attribute of a module in some directories.

    Args:
        root: The repository root.
        directories: Directories under it to read (``tests``, ``scripts``).
        module: The dotted module name.

    Returns:
        The replacements, ordered by file and line.
    """
    found: list[PatchSite] = []
    for directory in directories:
        for file in sorted((root / directory).rglob("*.py")):
            if "__pycache__" in file.parts or "node_modules" in file.parts:
                continue
            try:
                tree = ast.parse(file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            found.extend(patches_of(tree, module, file.relative_to(root).as_posix()))
    return found
