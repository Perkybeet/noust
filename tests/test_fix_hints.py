"""
Every command and route a message tells the operator to use exists.

The security check ``sys.degraded`` said "run ``noust server units --failed``" and
pointed at ``GET /api/server/units?state=failed``; neither existed, so the fix a
finding offered was the one thing on the page that could not be followed. A hint
is only ever read by a person who already has a problem, so a dead one is worse
than none, and it is invisible to every other test: it is a string.

This is the chokepoint for that class of defect. It reads every string literal
in the package (docstrings aside) and holds each ``noust ...`` command to the
real Click tree (the subcommands, and the long options each one accepts), and
each ``GET /api/...`` route to the application's real route table (the method,
the path, and the query parameters it declares). A hint written as a variable
cannot be read, so the builders of the security checks' fixes are held to take
literals. A path with no verb in front of it is not read: that is how the
package also names URLs that are not its own routes. The console's own copy
(``panel/src/i18n``) says "run noust ..." too, and is held to the same tree.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import click
import pytest

import noust
from noust.cli.app import cli
from noust.core.accounts import AuthPolicy
from noust.web.api.openapi import api_routes
from noust.web.auth import SecurityConfig
from noust.web.server import create_app

SRC = Path(noust.__file__).resolve().parent
REPO = SRC.parent.parent

#: Where a command starts inside a sentence: not part of a path, a unit name or a
#: dotted name, and followed by a space.
COMMAND_START = re.compile(r"(?<![\w/.\-])noust(?= )")

#: ``GET /api/x`` with an optional query, as it is written in a message.
ENDPOINT = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE) (/[A-Za-z0-9_\-/{}<>.]*)(?:\?([A-Za-z0-9_=&.\-<>]*))?"
)

#: Words that come after a command in a sentence and are not part of it.
PUNCTUATION = "()'\"`,;:."


def literals(path: Path) -> Iterator[tuple[int, str]]:
    """
    Yield the string literals of a module that are not docstrings.

    An f-string is read with each substitution replaced by ``<x>``, which is how a
    placeholder looks to the person reading the message.

    Args:
        path: The module.

    Yields:
        The line and the text of each literal.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            # Its pieces are read as one text below, not again on their own.
            docstrings.update(id(part) for part in node.values)
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            yield (
                node.lineno,
                "".join(
                    str(part.value) if isinstance(part, ast.Constant) else "<x>"
                    for part in node.values
                ),
            )


def package_literals() -> Iterator[tuple[str, int, str]]:
    """
    Yield every non-docstring literal of the package with where it is.

    Yields:
        The repository-relative path, the line and the text.
    """
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = str(path.relative_to(REPO))
        for line, text in literals(path):
            yield name, line, text


# The command tree ---------------------------------------------------------


def long_options(command: click.Command) -> set[str]:
    """
    Name the options a command accepts.

    Args:
        command: A Click command or group.

    Returns:
        Every spelling of every option, long and short.
    """
    names = {"--help", "-h"}
    for param in command.params:
        names.update(getattr(param, "opts", []))
        names.update(getattr(param, "secondary_opts", []))
    return names


def resolve_command(text: str) -> str | None:
    """
    Follow a ``noust ...`` sentence through the Click tree.

    The words are read while they name subcommands; at a command, the rest is
    arguments or prose and only the options are held to what the command
    accepts. An option is accepted if the command or any group above it declares
    it, because a global flag may sit on either side of the subcommand.

    Args:
        text: The text from the word ``noust`` up to the next command or the end.

    Returns:
        What is wrong with it, or None when every word resolves.
    """
    words = text.split()
    current: click.Command = cli
    context = click.Context(cli)
    accepted = long_options(cli)
    path = ["noust"]
    for raw in words[1:]:
        if raw.startswith("(") or raw in ("-", "--"):
            return None  # the sentence moved on: a parenthesis, a bullet
        word = raw.strip(PUNCTUATION)
        if word.startswith("<") and isinstance(current, click.Group):
            return None  # a subcommand the message fills in
        if word.startswith("-"):
            option = word.split("=", 1)[0]
            if option not in accepted | long_options(current):
                return f"'{' '.join(path)}' has no option {option}"
        elif word and not word.startswith("<") and not word.isupper():
            if not isinstance(current, click.Group):
                return None
            found = current.get_command(context, word)
            if found is None:
                return f"'{' '.join(path)}' has no command {word}"
            current = found
            path.append(word)
            accepted |= long_options(found)
            context = click.Context(found, parent=context)
        if raw[-1] in "'\"`,;.)":
            return None  # a closing quote or punctuation ends the command
    return None


def commands_in(texts: Iterable[tuple[str, int, str]]) -> Iterator[tuple[str, int, str]]:
    """
    Yield every ``noust ...`` command a set of texts tells a reader to run.

    Args:
        texts: Where each text is (a file), its line and the text.

    Yields:
        Where it is and the command, up to the next one in the same text.
    """
    commands = set(cli.list_commands(click.Context(cli)))
    for name, line, text in texts:
        starts = [match.start() for match in COMMAND_START.finditer(text)]
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(text)
            hint = text[start:end]
            words = hint.split()
            first = words[1].strip(PUNCTUATION) if len(words) > 1 else ""
            # "noust is the console" is a sentence about the product, not a command: a
            # command is one whose first word is a command, or a literal that is one.
            if first in commands or start == 0:
                yield name, line, hint.strip()


def dead_commands(texts: Iterable[tuple[str, int, str]]) -> list[str]:
    """
    Name the commands in some texts that the Click tree does not have.

    Args:
        texts: See :func:`commands_in`.

    Returns:
        One line per dead command, with where it is and what is wrong.
    """
    dead: dict[str, None] = {}
    for name, line, hint in commands_in(texts):
        problem = resolve_command(hint)
        if problem:
            dead[f"{name}:{line}: {hint[:100]!r}: {problem}"] = None
    return list(dead)


def test_every_command_a_message_names_exists() -> None:
    dead = dead_commands(package_literals())
    assert not dead, (
        "These messages tell the reader to run a command that does not exist. Point them at "
        "the real one (or add the command), because nothing else would notice:\n" + "\n".join(dead)
    )


def catalog_lines() -> Iterator[tuple[str, int, str]]:
    """
    Yield the lines of the console's message catalogs, English and Spanish.

    Yields:
        The repository-relative path, the line and the text.
    """
    for path in sorted((REPO / "panel" / "src" / "i18n").glob("*/*.ts")):
        for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            yield str(path.relative_to(REPO)), line, text


@pytest.mark.skipif(not (REPO / "panel" / "src" / "i18n").is_dir(), reason="no console source")
def test_every_command_the_console_names_exists() -> None:
    """The console's copy says "run noust ..." too, and nothing else would notice."""
    dead = dead_commands(catalog_lines())
    assert not dead, "The console's catalogs name commands that do not exist:\n" + "\n".join(dead)


# The route table ----------------------------------------------------------


def normalise(path: str) -> str:
    """
    Make a path comparable: parameters are ``{}``, no trailing slash or full stop.

    Args:
        path: A route template or a path from a message.

    Returns:
        The path.
    """
    path = re.sub(r"\{[^}]*\}|<[^>]*>", "{}", path)
    return path.rstrip(".").rstrip("/") or "/"


@pytest.fixture
def served(sandbox: Path) -> dict[tuple[str, str], set[str]]:
    """
    The routes the console serves.

    Args:
        sandbox: The isolated filesystem root.

    Returns:
        ``(METHOD, normalised path)`` to the query parameters the route declares.
    """
    app: Any = create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
        )
    )
    schema = app.openapi()
    table: dict[tuple[str, str], set[str]] = {}
    for route in api_routes(app.routes):
        template = str(route.path_format)
        operations = schema["paths"].get(template, {})
        for method in route.methods or ():
            operation = operations.get(method.lower(), {})
            query = {
                parameter["name"]
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "query"
            }
            table[(method, normalise(template))] = query
    return table


def test_every_route_a_message_names_exists(served: dict[tuple[str, str], set[str]]) -> None:
    dead = []
    for name, line, text in package_literals():
        for match in ENDPOINT.finditer(text):
            method, path, query = match.group(1), normalise(match.group(2)), match.group(3)
            if not path.startswith("/api/"):
                continue
            where = f"{name}:{line}: {method} {match.group(2)}"
            if (method, path) not in served:
                other = sorted(m for m, p in served if p == path)
                dead.append(
                    f"{where}: no such route"
                    + (f" (that path answers {', '.join(other)})" if other else "")
                )
                continue
            for key in (part.split("=", 1)[0] for part in (query or "").split("&") if part):
                if key not in served[(method, path)]:
                    dead.append(f"{where}: the route declares no query parameter {key!r}")
    assert not dead, (
        "These messages name a route that is not served, or a query it does not read:\n"
        + "\n".join(dead)
    )


# The builders -------------------------------------------------------------


def calls(node: ast.AST, names: set[str], skip: set[str]) -> Iterator[ast.Call]:
    """
    Yield the calls of the given functions below a node.

    Args:
        node: The module, or any node of it.
        names: Function names, as called.
        skip: Functions whose bodies are not read: the builders themselves, which
            pass their own parameters on.

    Yields:
        The call nodes.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef) and child.name in skip:
            continue
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id in names
        ):
            yield child
        yield from calls(child, names, skip)


def is_text(node: ast.expr) -> bool:
    """
    Tell whether a node is a string the guard can read.

    Args:
        node: An expression.

    Returns:
        True for a literal or an f-string.
    """
    return isinstance(node, ast.JoinedStr) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def test_a_fix_names_its_command_and_route_where_the_guard_can_read_them() -> None:
    """A hint built from a variable is one no test above can hold to the real tree."""
    path = SRC / "managers" / "server" / "security_checks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    unreadable = []
    for call in calls(tree, {"_action"}, skip=set()):
        arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
        if len(arguments) != 3 or not all(is_text(argument) for argument in arguments):
            unreadable.append(f"line {call.lineno}: _action(summary, cli, endpoint) takes literals")
    for call in calls(tree, {"CheckFix"}, skip={"_action"}):
        for keyword in call.keywords:
            if keyword.arg in ("cli", "endpoint") and not is_text(keyword.value):
                unreadable.append(f"line {call.lineno}: CheckFix({keyword.arg}=...) is not literal")
    assert not unreadable, "\n".join(unreadable)
