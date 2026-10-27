# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The import graph of ``src/noust``, read from the syntax tree and never by importing.

Importing the modules to learn who imports whom would run their side effects
and would see only the order of this one process, which is how a cycle that
works today and breaks when the order changes goes unnoticed. The graph is
built from ``ast``: each ``import`` statement becomes an edge, and says whether
it runs when the module is loaded, when a function is called, or only for a
type checker.

An edge names what the statement writes: ``import a.b.c`` is an edge to
``a.b.c``, ``from a.b import c`` is an edge to ``a.b.c`` when that is a module
and to ``a.b`` when ``c`` is a name inside it. The package initialisers that
Python runs on the way are not edges of their own.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: Runs when the importing module is loaded.
MODULE = "module"
#: Runs when a function that contains it is called.
LAZY = "lazy"
#: Read by a type checker, never executed.
TYPING = "typing"


@dataclass(frozen=True)
class Edge:
    """
    One import statement that reaches another module of the package.

    Attributes:
        source: The importing module, dotted.
        target: The module it reaches, dotted.
        kind: ``MODULE``, ``LAZY`` or ``TYPING``.
        line: The statement's line.
    """

    source: str
    target: str
    kind: str
    line: int


def package_modules(root: Path) -> dict[str, Path]:
    """
    Map every module of a package to its file.

    Args:
        root: The package directory (``src/noust``).

    Returns:
        Dotted module name to path; a package is its ``__init__.py``.
    """
    modules: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = [root.name, *path.relative_to(root).with_suffix("").parts]
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _Collector(ast.NodeVisitor):
    """Walk one module's tree keeping where each import statement sits."""

    def __init__(self, module: str, is_package: bool, known: set[str]) -> None:
        self.module = module
        self.package = module if is_package else module.rpartition(".")[0]
        self.known = known
        self.edges: list[Edge] = []
        self._kind = MODULE

    def _nested(self, kind: str, node: ast.AST) -> None:
        outer, self._kind = self._kind, kind
        self.generic_visit(node)
        self._kind = outer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._nested(LAZY, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._nested(LAZY, node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._nested(LAZY, node)

    def visit_If(self, node: ast.If) -> None:
        if not _is_type_checking(node.test):
            self.generic_visit(node)
            return
        # The else branch of ``if TYPE_CHECKING`` is what runs.
        outer = self._kind
        self._kind = TYPING if outer == MODULE else outer
        for child in node.body:
            self.visit(child)
        self._kind = outer
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target = self._longest_known(alias.name)
            if target is not None:
                self._add(target, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._base(node)
        if base is None:
            return
        for alias in node.names:
            submodule = f"{base}.{alias.name}"
            if submodule in self.known:
                self._add(submodule, node)
            elif base in self.known:
                self._add(base, node)

    def _base(self, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        parts = self.package.split(".")
        if node.level - 1 >= len(parts):
            return None
        anchor = parts[: len(parts) - (node.level - 1)]
        return ".".join([*anchor, node.module] if node.module else anchor)

    def _longest_known(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.known:
                return candidate
        return None

    def _add(self, target: str, node: ast.stmt) -> None:
        if target != self.module:
            self.edges.append(Edge(self.module, target, self._kind, node.lineno))


def read_edges(root: Path) -> list[Edge]:
    """
    Read every import statement of a package that reaches another of its modules.

    Args:
        root: The package directory (``src/noust``).

    Returns:
        The edges, in file and line order.
    """
    modules = package_modules(root)
    known = set(modules)
    edges: list[Edge] = []
    for name, path in modules.items():
        collector = _Collector(name, path.name == "__init__.py", known)
        collector.visit(ast.parse(path.read_text(encoding="utf-8")))
        edges.extend(collector.edges)
    return edges


def layer(module: str, modules: dict[str, Path]) -> str:
    """
    Name the first-level package a module belongs to.

    Args:
        module: A dotted module name starting with the package's own.
        modules: What ``package_modules`` returned, to tell ``noust.core`` (a package)
            from ``noust.main`` (a module that sits in the root).

    Returns:
        ``core`` for ``noust.core.store`` and for ``noust.core``; ``""`` for the
        package itself and for a module directly in it.
    """
    parts = module.split(".")
    if len(parts) >= 3 or (len(parts) == 2 and modules[module].name == "__init__.py"):
        return parts[1]
    return ""


def strongly_connected(edges: list[Edge], kinds: frozenset[str]) -> list[list[str]]:
    """
    Find the groups of modules that import each other, directly or through others.

    Args:
        edges: The package's edges.
        kinds: Which kinds of statement count; a cycle that exists only when a
            function is called is not one of them for ``{MODULE}``.

    Returns:
        Each group of more than one module (or a module that imports itself),
        sorted, the groups sorted by their first module.
    """
    graph: dict[str, set[str]] = {}
    for edge in edges:
        if edge.kind in kinds:
            graph.setdefault(edge.source, set()).add(edge.target)
            graph.setdefault(edge.target, set())

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    groups: list[list[str]] = []
    counter = 0

    # Iterative Tarjan: the graph is deeper than Python's recursion limit allows.
    for start in sorted(graph):
        if start in index:
            continue
        work: list[tuple[str, list[str]]] = [(start, sorted(graph[start]))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, pending = work[-1]
            if pending:
                nxt = pending.pop()
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(graph[nxt])))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                group = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                if len(group) > 1:
                    groups.append(sorted(group))

    return sorted(groups)


def closure(edges: list[Edge], start: str, kinds: frozenset[str]) -> set[str]:
    """
    List every module that loading one module loads, through the statements written.

    Args:
        edges: The package's edges.
        start: The module to start from.
        kinds: Which kinds of statement count.

    Returns:
        The modules reached, ``start`` included.
    """
    forward: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind in kinds:
            forward.setdefault(edge.source, []).append(edge.target)
    seen = {start}
    pending = [start]
    while pending:
        for target in forward.get(pending.pop(), ()):
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen
