# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The CLI's start-up budget: what ``noust --version`` and ``noust --help`` import before they answer.

Every invocation pays for the modules the entry point imports at module level, whatever the
command is, and an operator types ``noust`` many times a day on a laptop and in CI. A command's
module is imported when it is run (``LazyGroup``), and what a command needs it imports when it
needs it; this test holds the part that is not lazy. It reads the source and never starts a
process (``tests/conftest.py`` forbids it), so it follows the imports the interpreter would run
when the module loads: the statements at module level, the ``__init__`` of every parent package,
and the submodule a ``from package import name`` takes when ``name`` is one. An import inside a
function, or under ``if TYPE_CHECKING``, is not part of the start-up and is not followed.

The budget is a count of Noust modules, not a time: a count does not depend on the machine the
test runs on, and the time follows it (see ``BUDGET_MEASURED``).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

#: The modules the console script and ``python -m noust`` start from.
ENTRY_POINTS = ("noust.main", "noust.cli.app")

#: Modules a command line that only asks for the version or the help must never load, each with
#: the cost that earned it a place here. The budget below catches the total; this names the
#: culprit when somebody puts one of them back at module level.
HEAVY = {
    "noust.core.store": "the SQLite store with its migrations (about 90 modules with its tables)",
    "noust.core.notifier": "the notification channels, which reach the network clients",
    "noust.managers.name_migration": "the renaming of 4.0, which reaches every manager",
    "noust.web.server": "the web application and its routers",
}

#: Noust modules that ``noust.main`` and ``noust.cli.app`` load between them at module level,
#: counted by this walker on the commit that set the budget (4.1.0). Measured with
#: ``python -X importtime -m noust --version`` (median of 5 runs, a laptop with the load of a
#: working day): before this budget the command loaded 298 Noust modules and took 574 ms, because
#: ``noust/__init__.py`` and ``noust/core/__init__.py`` imported the configuration, the store and
#: the notifier to re-export their classes, and ``entrypoint()`` imported the renaming of 4.0
#: (about 200 modules) before it knew whether it would run; after it, 20 modules and 115 ms.
BUDGET_MEASURED = 16

#: The measure plus half of it. A module joining the closure of the entry points is a decision
#: somebody should take on purpose, and half again leaves room for the ordinary growth of the few
#: modules that are in it (a helper the logger or the runner starts to need) but not for a new
#: subsystem, which brings a hundred. Raise it only after measuring ``--version`` and ``--help``
#: again, and write the figures in the commit.
BUDGET = BUDGET_MEASURED * 3 // 2


def _module_file(module: str) -> Path | None:
    """
    Find the source file of a Noust module.

    Args:
        module: The dotted name.

    Returns:
        The ``.py`` file, or the ``__init__.py`` of the package; None when it is not Noust's.
    """
    relative = Path(*module.split("."))
    package = SRC / relative / "__init__.py"
    if package.is_file():
        return package
    plain = SRC / relative.with_suffix(".py")
    return plain if plain.is_file() else None


def _is_type_checking(test: ast.expr) -> bool:
    """
    Whether an ``if`` test is ``TYPE_CHECKING`` (bare or through ``typing``).

    Args:
        test: The condition of the ``if``.

    Returns:
        True for ``TYPE_CHECKING`` and ``typing.TYPE_CHECKING``.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _executed_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """
    Walk the statements that run when a module loads, without entering a function.

    Args:
        body: Statements of a module, or of a block inside it.

    Yields:
        Every statement that executes at import, nested blocks and class bodies included; the
        ``if TYPE_CHECKING`` branch is skipped and its ``else`` followed.
    """
    for node in body:
        yield node
        if isinstance(node, ast.If):
            if _is_type_checking(node.test):
                yield from _executed_statements(node.orelse)
            else:
                yield from _executed_statements(node.body)
                yield from _executed_statements(node.orelse)
        elif isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody):
                yield from _executed_statements(block)
            for handler in node.handlers:
                yield from _executed_statements(handler.body)
        elif isinstance(node, (ast.With, ast.For, ast.While, ast.ClassDef)):
            yield from _executed_statements(node.body)


def _with_parents(module: str) -> list[str]:
    """
    List a module and the packages whose ``__init__`` runs before it.

    Args:
        module: A dotted name.

    Returns:
        ``a``, ``a.b`` and ``a.b.c`` for ``a.b.c``.
    """
    parts = module.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


@cache
def direct_imports(module: str) -> frozenset[str]:
    """
    Name the Noust modules loading one module loads, one step down.

    Args:
        module: A dotted name of a module of Noust.

    Returns:
        The modules its module-level imports reach, with the parents of each. A name taken with
        ``from package import name`` counts as a module when ``name`` is a submodule, the
        conservative reading: the import system tries the attribute first, so a package that
        defines the name itself would not load the file, and no package of Noust does that for a
        name that is also a module.
    """
    path = _module_file(module)
    if path is None:
        return frozenset()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in _executed_statements(tree.body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.update(_with_parents(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")
                base_parts = base_parts[: len(base_parts) - (node.level - 1)]
                base = ".".join([*base_parts, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            found.update(_with_parents(base))
            for alias in node.names:
                if _module_file(f"{base}.{alias.name}") is not None:
                    found.update(_with_parents(f"{base}.{alias.name}"))
    return frozenset(name for name in found if _module_file(name) is not None and name != module)


def closure(roots: tuple[str, ...]) -> dict[str, str | None]:
    """
    Follow the module-level imports from some modules to everything they load.

    Args:
        roots: Dotted names to start from.

    Returns:
        Every Noust module loaded, to the module that first reached it (None for a root), so a
        failure can say how a module got in.
    """
    loaded: dict[str, str | None] = {}
    pending: list[tuple[str, str | None]] = []
    for root in roots:
        for name in _with_parents(root):
            pending.append((name, None if name == root else root))
    while pending:
        name, via = pending.pop(0)
        if name in loaded or _module_file(name) is None:
            continue
        loaded[name] = via
        for imported in sorted(direct_imports(name)):
            if imported not in loaded:
                pending.append((imported, name))
    return loaded


def chain(loaded: dict[str, str | None], module: str) -> str:
    """
    Spell the imports that lead from an entry point to a module.

    Args:
        loaded: The result of :func:`closure`.
        module: A module in it.

    Returns:
        ``noust.main -> noust.cli.app -> ... -> module``.
    """
    steps = [module]
    while (via := loaded.get(steps[-1])) is not None:
        steps.append(via)
    return " -> ".join(reversed(steps))


@pytest.fixture(scope="module")
def startup() -> dict[str, str | None]:
    """The Noust modules the entry points load at module level."""
    return closure(ENTRY_POINTS)


class TestStartupBudget:
    """The entry points import a bounded, small part of Noust."""

    def test_the_closure_stays_within_the_budget(self, startup: dict[str, str | None]) -> None:
        """
        The count of modules ``noust --version`` loads does not grow past the budget.

        The failure says which direct import of the entry points brought the most modules, so
        the person who broke it starts from the right place.
        """
        if len(startup) <= BUDGET:
            return
        by_branch: dict[str, int] = {}
        for module in startup:
            branch = chain(startup, module).split(" -> ")
            key = branch[2] if len(branch) > 2 else branch[-1]
            by_branch[key] = by_branch.get(key, 0) + 1
        heaviest = sorted(by_branch.items(), key=lambda pair: -pair[1])[:5]
        listing = "\n".join(f"  {count:4d} modules through {name}" for name, count in heaviest)
        pytest.fail(
            f"noust.main and noust.cli.app load {len(startup)} Noust modules at module level; "
            f"the budget is {BUDGET}. Heaviest branches:\n{listing}\n"
            "Import what a command needs inside the command, as LazyGroup does for the "
            "commands themselves."
        )

    @pytest.mark.parametrize("module", sorted(HEAVY))
    def test_a_heavy_module_is_not_loaded_at_start(
        self, startup: dict[str, str | None], module: str
    ) -> None:
        """A module known to be expensive is imported when it is used, never at start."""
        assert module not in startup, (
            f"{module} ({HEAVY[module]}) is imported at module level by the entry points: "
            f"{chain(startup, module)}. Import it where it is used."
        )


class TestTheWalker:
    """The walker follows the imports the interpreter runs, and only those."""

    def test_it_follows_the_parents_of_a_package(self) -> None:
        """Importing ``noust.cli.app`` runs ``noust``'s and ``noust.cli``'s ``__init__`` first."""
        loaded = closure(("noust.cli.app",))
        assert {"noust", "noust.cli", "noust.cli.app"} <= set(loaded)

    def test_it_skips_a_function_body_and_type_checking(self, tmp_path: Path) -> None:
        """A deferred import is not part of the start-up, and neither is one for the types."""
        source = (
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING\n"
            "import noust.core.paths\n"
            "if TYPE_CHECKING:\n"
            "    import noust.core.store\n"
            "def late() -> None:\n"
            "    import noust.core.notifier\n"
        )
        tree = ast.parse(source)
        names = {
            alias.name
            for node in _executed_statements(tree.body)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert names == {"noust.core.paths"}

    def test_it_takes_the_submodule_a_from_import_names(self) -> None:
        """``from noust.core import fs`` loads ``noust.core.fs`` as well as the package."""
        assert "noust.core.fs" in closure(("noust.cli.app",))
