# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The CLI works on a machine without the console's dependencies.

FastAPI and friends are optional: the RPM only suggests them and ``pip install
wasm-cli`` without the ``web`` extra leaves them out. ``wasm --help`` loads every
command module to list it, so one module importing the web package at the top
made the whole CLI crash with ``No module named 'fastapi'`` there. Found by the
release gate's RPM builds for 2.0.0.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from noust.core.exceptions import DependencyError

SRC = Path(__file__).resolve().parent.parent / "src" / "noust"

#: What only the console needs. Importing any of it at module level outside
#: wasm/web makes every command depend on the console being installed.
CONSOLE_ONLY = ("noust.web", "fastapi", "starlette", "uvicorn")


def _module_level_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """
    List the modules a file imports when it is imported, not when a function runs.

    Imports under ``if TYPE_CHECKING:`` never run and are left out.

    Args:
        tree: The parsed file.

    Returns:
        (line, module) for each import statement at module level.
    """
    found: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
            if isinstance(inner, ast.Import):
                found.extend((inner.lineno, alias.name) for alias in inner.names)
            elif isinstance(inner, ast.ImportFrom) and inner.module and inner.level == 0:
                found.append((inner.lineno, inner.module))
    return found


def test_nothing_outside_the_console_imports_it_at_module_level() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        if relative.parts[0] == "web":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, module in _module_level_imports(tree):
            if module == "noust.web" or module.startswith(CONSOLE_ONLY):
                offenders.append(f"{relative}:{line} imports {module}")
    assert not offenders, "Import these inside the function that needs them:\n" + "\n".join(
        offenders
    )


def test_a_console_command_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.cli.web_state import token_manager

    # None in sys.modules makes the import statement raise ImportError.
    monkeypatch.setitem(sys.modules, "noust.web.auth", None)

    with pytest.raises(DependencyError) as caught:
        token_manager()

    assert "noust[web]" in caught.value.details


#: Every optional dependency: the console's stack and what only some commands
#: use. The RPM only suggests them, and a pip install without extras has none.
OPTIONAL = (
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "httpx",
    "psutil",
    "websockets",
    "cryptography",
)


@pytest.fixture
def without_optional_dependencies(monkeypatch: pytest.MonkeyPatch):
    """
    A fresh import of noust on an interpreter that has none of them.

    None in sys.modules makes an import statement raise ImportError. Every
    module is put back afterwards, so no other test sees the fresh copies.
    """
    saved = dict(sys.modules)
    for name in list(sys.modules):
        if name == "noust" or name.startswith("noust."):
            del sys.modules[name]
        elif name.split(".")[0] in OPTIONAL:
            del sys.modules[name]
    for name in OPTIONAL:
        sys.modules[name] = None  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_every_command_loads_without_any_optional_dependency(
    without_optional_dependencies: None,
) -> None:
    """``noust --help`` loads every command module; none may need an extra to be listed."""
    import click

    from noust.cli.app import cli

    def walk(command: click.Command) -> int:
        count = 1
        if isinstance(command, click.Group):
            ctx = click.Context(command)
            for name in command.list_commands(ctx):
                sub = command.get_command(ctx, name)
                assert sub is not None, name
                count += walk(sub)
        return count

    assert walk(cli) > 100
