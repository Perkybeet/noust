# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Finding the English that Noust types into an error a person reads (item 151).

The console speaks English and Spanish, and a sentence it is only handed as a string can only be
shown as it came. An error that crosses the API (and a job, which keeps the error that ended it) is
translated by its code (``noust.core.server_text``), so a ``raise`` that spells its sentence in
English and carries no code is a Spanish console with an English paragraph in it. This reads the
syntax tree of the package and finds each such ``raise``:

* ``raise X("...")`` where ``X`` is a :class:`noust.core.exceptions.NoustError` (any subclass,
  resolved by name from the class definitions of the package) and the message is text the source
  spells (a literal, an f-string, a concatenation or a ``.format``) and no ``coded=`` is given;
* ``raise HTTPException(...)`` whose ``detail`` is text the source spells, or a mapping with a
  ``detail`` and no ``coded``.

What is not counted, on purpose: a message that is a variable (it may be a program's own output,
which is shown as it came), the command line (``cli/``, which speaks English only), and an
exception that is not a Noust error (a ``ValueError`` is a defect that answers 500).
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import noust

ROOT = Path(noust.__file__).resolve().parent

#: The command line prints English and nothing translates it.
EXCLUDED_PARTS = ("cli", "static")


def _spelled(node: ast.expr | None) -> bool:
    """
    Args:
        node: The message of a raise.

    Returns:
        Whether the source spells the text out: a literal, an f-string, a concatenation with one
        of those, or a ``.format`` / ``%`` over one.
    """
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp):
        return _spelled(node.left) or _spelled(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "format" and _spelled(node.func.value)
    return False


def _call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def noust_error_names(trees: dict[Path, ast.Module]) -> set[str]:
    """
    Args:
        trees: The parsed modules of the package.

    Returns:
        The names of every class that is a NoustError or derives from one, by name.
    """
    bases: dict[str, set[str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    base.id if isinstance(base, ast.Name) else base.attr
                    for base in node.bases
                    if isinstance(base, ast.Name | ast.Attribute)
                )
    names = {"NoustError"}
    grew = True
    while grew:
        grew = False
        for name, parents in bases.items():
            if name not in names and parents & names:
                names.add(name)
                grew = True
    return names


def classes_that_do_not_take_a_message(trees: dict[Path, ast.Module]) -> set[str]:
    """
    Args:
        trees: The parsed modules of the package.

    Returns:
        The classes whose constructor takes something else first (``AuthenticationFailed("bad_password")``
        names a reason for the audit trail, not a sentence a person reads), by name.
    """
    bases: dict[str, set[str]] = {}
    first: dict[str, str] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases.setdefault(node.name, set()).update(
                base.id if isinstance(base, ast.Name) else base.attr
                for base in node.bases
                if isinstance(base, ast.Name | ast.Attribute)
            )
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    names = [argument.arg for argument in item.args.args]
                    if len(names) > 1:
                        first[node.name] = names[1]

    def takes_message(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name in first:
            return first[name] == "message"
        if name in seen:
            return True
        return (
            all(takes_message(parent, seen | {name}) for parent in bases.get(name, ()))
            if bases.get(name)
            else True
        )

    return {name for name in bases if not takes_message(name)}


def _http_detail(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "detail":
            return keyword.value
    return call.args[1] if len(call.args) >= 2 else None


def _http_is_untranslated(detail: ast.expr | None) -> bool:
    if isinstance(detail, ast.Dict):
        keys = {key.value for key in detail.keys if isinstance(key, ast.Constant)}
        if "coded" in keys:
            return False
        for key, value in zip(detail.keys, detail.values, strict=False):
            if isinstance(key, ast.Constant) and key.value == "detail":
                return _spelled(value)
        return False
    return _spelled(detail)


@functools.cache
def parsed_package() -> dict[Path, ast.Module]:
    """
    Returns:
        Every module of the package that is not the command line, parsed.
    """
    trees: dict[Path, ast.Module] = {}
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts):
            continue
        trees[path] = ast.parse(path.read_text(encoding="utf-8"))
    return trees


def count_in(
    tree: ast.Module, errors: set[str], no_message: frozenset[str] | set[str] = frozenset()
) -> int:
    """
    Args:
        tree: One parsed module.
        errors: The names of the Noust errors (:func:`noust_error_names`).
        no_message: The classes whose first argument is not a sentence
            (:func:`classes_that_do_not_take_a_message`).

    Returns:
        How many of its raises say English the source spells with no code.
    """
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        name = _call_name(node.exc)
        if name is not None and name.endswith("HTTPException"):
            count += _http_is_untranslated(_http_detail(node.exc))
        elif name in errors:
            if name in no_message or any(keyword.arg == "coded" for keyword in node.exc.keywords):
                continue
            message = node.exc.args[0] if node.exc.args else None
            if message is None:
                message = next((k.value for k in node.exc.keywords if k.arg == "message"), None)
            count += _spelled(message)
    return count


@functools.cache
def untranslated_by_file() -> dict[str, int]:
    """
    Returns:
        For each module (relative to the package, with forward slashes) that raises an error whose
        sentence is English the source spells and no code stands for, how many such raises.
    """
    trees = parsed_package()
    errors = noust_error_names(trees)
    no_message = classes_that_do_not_take_a_message(trees)
    found: dict[str, int] = {}
    for path, tree in trees.items():
        count = count_in(tree, errors, no_message)
        if count:
            found[path.relative_to(ROOT).as_posix()] = count
    return found
