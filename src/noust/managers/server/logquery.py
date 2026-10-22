# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A query language for the journal: fields, ``AND``, ``OR``, ``-``, quotes and groups.

Looking for one failure in a day of noise is not a substring search. ``@unit:nginx error -timeout``
is the question an operator has in their head, and ``error AND (@unit:nginx OR @app:shop.example.com)``
the one after it. This is that question, read once and judged here, on the server, against what
``journalctl`` returned: no shell, no pattern engine (a regular expression typed into a
console is how a read becomes a denial of service), and nothing a person typed ever becomes part
of a command line except through the checks of :class:`~noust.managers.server.journal.JournalReader`.

=============  ============================================================  =========================
syntax         means                                                          example
=============  ============================================================  =========================
``word``       the message contains it, ignoring case                          ``timeout``
``"a phrase"`` the message contains exactly that                              ``"connection refused"``
``@unit:x``    logged by that unit (``.service`` optional, ``*`` is a wildcard) ``@unit:noust-*``
``@app:d``     logged by the units of that application                         ``@app:shop.example.com``
``@level:l``   that level or worse (``err``, ``warning``, ... or 0 to 7)       ``@level:err``
``@pid:n``     logged by that process                                          ``@pid:4821``
``-x``         not x (``NOT x`` too)                                           ``-debug``
``x y``        both (``x AND y``)                                              ``error timeout``
``x OR y``     either                                                          ``error OR fatal``
``( ... )``    grouping                                                        ``(a OR b) c``
=============  ============================================================  =========================

``AND``, ``OR`` and ``NOT`` are the upper-case words; ``or`` in lower case is a word to look for.
Precedence is ``NOT``, then ``AND``, then ``OR``. An empty query matches everything.

The parts of a query that ``journalctl`` can narrow by itself (a unit, an application's units, a
level) are handed to it so it reads less; the whole query is always judged here again, so the answer
never depends on what was pushed down.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from noust.core.exceptions import ValidationError

#: Longest query accepted, in characters.
MAX_QUERY = 500

#: Most terms, fields and operators one query may hold.
MAX_NODES = 40

#: Deepest nesting of parentheses and negations.
MAX_DEPTH = 8

#: Most ``*`` a unit pattern may hold.
MAX_WILDCARDS = 4

#: The fields a query may name, and the words that stand for them.
FIELDS = {
    "unit": "unit",
    "service": "unit",
    "app": "app",
    "level": "level",
    "priority": "level",
    "pid": "pid",
}

#: The levels by name, with the spellings people use.
LEVELS = {
    "emerg": 0,
    "alert": 1,
    "crit": 2,
    "critical": 2,
    "err": 3,
    "error": 3,
    "warning": 4,
    "warn": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
}

SYNTAX = (
    'Words, "phrases", @unit:, @app:, @level:, @pid:, - or NOT to exclude, AND, OR and (groups). '
    "Example: @unit:nginx error -timeout"
)


@dataclass(frozen=True)
class Term:
    """A word or phrase the message must contain, ignoring case."""

    text: str


@dataclass(frozen=True)
class Field:
    """``@name:value``: a property of the entry, not of its message."""

    name: str
    value: str


@dataclass(frozen=True)
class Not:
    """The opposite of a clause."""

    node: Node


@dataclass(frozen=True)
class And:
    """Every clause."""

    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class Or:
    """Any clause."""

    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class Anything:
    """The empty query: everything."""


Node = Term | Field | Not | And | Or | Anything


@dataclass(frozen=True)
class Token:
    """One piece of the query text."""

    kind: str
    text: str
    at: int


class QueryError(ValidationError):
    """A query that cannot be read, saying where and what the syntax is."""

    def __init__(self, message: str, at: int | None = None) -> None:
        where = f" (at character {at + 1})" if at is not None else ""
        super().__init__(f"{message}{where}", SYNTAX)


_WORD = re.compile(r'[^\s()"]+')
_FIELD = re.compile(r'@([A-Za-z]+):("([^"]*)"|[^\s()"]*)')


def tokenize(text: str) -> list[Token]:
    """
    Cut a query into words, phrases, parentheses and operators.

    Args:
        text: The query.

    Returns:
        The tokens, in order.

    Raises:
        QueryError: The query is too long or a quote is not closed.
    """
    if len(text) > MAX_QUERY:
        raise QueryError(f"The query is longer than {MAX_QUERY} characters")
    tokens: list[Token] = []
    at = 0
    while at < len(text):
        char = text[at]
        if char.isspace():
            at += 1
        elif char in "()":
            tokens.append(Token(char, char, at))
            at += 1
        elif char == '"':
            end = text.find('"', at + 1)
            if end == -1:
                raise QueryError("A quote is not closed", at)
            tokens.append(Token("phrase", text[at + 1 : end], at))
            at = end + 1
        elif char == "-" and at + 1 < len(text) and not text[at + 1].isspace():
            tokens.append(Token("not", "-", at))
            at += 1
        elif char == "@":
            match = _FIELD.match(text, at)
            if match is None:
                raise QueryError("A field is written @name:value, such as @unit:nginx", at)
            value = match.group(3) if match.group(3) is not None else match.group(2)
            tokens.append(Token("field", f"{match.group(1)}:{value}", at))
            at = match.end()
        else:
            word = _WORD.match(text, at)
            assert word is not None  # noqa: S101 - every other character is matched above
            token = word.group()
            kind = {"AND": "and", "OR": "or", "NOT": "not"}.get(token, "word")
            tokens.append(Token(kind, token, at))
            at = word.end()
    return tokens


class _Parser:
    """Recursive descent over the tokens, with a ceiling on size and depth."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.at = 0
        self.nodes = 0

    def peek(self) -> Token | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self) -> Token:
        token = self.tokens[self.at]
        self.at += 1
        return token

    def count(self, token: Token) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise QueryError(f"The query has more than {MAX_NODES} parts", token.at)

    def parse(self) -> Node:
        if not self.tokens:
            return Anything()
        node = self.or_(0)
        leftover = self.peek()
        if leftover is not None:
            raise QueryError(
                "Unexpected "
                + ("closing parenthesis" if leftover.kind == ")" else repr(leftover.text)),
                leftover.at,
            )
        return node

    def or_(self, depth: int) -> Node:
        parts = [self.and_(depth)]
        while (token := self.peek()) is not None and token.kind == "or":
            self.count(self.take())
            parts.append(self.and_(depth))
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def and_(self, depth: int) -> Node:
        parts = [self.not_(depth)]
        while (token := self.peek()) is not None and token.kind not in ("or", ")"):
            if token.kind == "and":
                self.take()
            parts.append(self.not_(depth))
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def not_(self, depth: int) -> Node:
        token = self.peek()
        if depth > MAX_DEPTH:
            raise QueryError(
                f"The query is nested more than {MAX_DEPTH} deep", token.at if token else None
            )
        if token is not None and token.kind == "not":
            self.count(self.take())
            return Not(self.not_(depth + 1))
        return self.atom(depth)

    def atom(self, depth: int) -> Node:
        token = self.peek()
        if token is None:
            raise QueryError("The query ends where a word, a field or a group was expected")
        if token.kind == "(":
            self.take()
            inner = self.or_(depth + 1)
            closing = self.peek()
            if closing is None or closing.kind != ")":
                raise QueryError("A parenthesis is not closed", token.at)
            self.take()
            return inner
        if token.kind in ("and", "or", ")"):
            raise QueryError(
                f"{token.text!r} needs something on both sides"
                if token.kind != ")"
                else "Unexpected closing parenthesis",
                token.at,
            )
        self.take()
        self.count(token)
        if token.kind == "field":
            name, _, value = token.text.partition(":")
            canonical = FIELDS.get(name.lower())
            if canonical is None:
                raise QueryError(
                    f"There is no field @{name}: use "
                    + ", ".join(f"@{field}" for field in dict.fromkeys(FIELDS.values())),
                    token.at,
                )
            if value == "":
                raise QueryError(f"@{name}: needs a value", token.at)
            _check_value(canonical, value, token.at)
            return Field(canonical, value)
        return Term(token.text)


def _check_value(name: str, value: str, at: int) -> None:
    """Refuse a field value that could never match, so a typo is told instead of returning nothing."""
    if (
        name == "level"
        and value.lower() not in LEVELS
        and not (value.isdigit() and int(value) <= 7)
    ):
        raise QueryError(
            f"@level:{value} is not a level: use " + ", ".join(LEVELS) + " or 0 to 7", at
        )
    if name == "pid" and not value.isdigit():
        raise QueryError(f"@pid:{value} is not a process number", at)
    if name == "unit" and value.count("*") > MAX_WILDCARDS:
        # A pattern is matched against every entry read; a handful of stars is a family of units,
        # dozens are a way to make the match itself the expensive part.
        raise QueryError(f"@unit:{value} has more than {MAX_WILDCARDS} wildcards", at)


def parse(text: str) -> Node:
    """
    Read a query.

    Args:
        text: What the operator typed.

    Returns:
        The clause tree; :class:`Anything` for an empty query.

    Raises:
        QueryError: It cannot be read, with where and what the syntax is.
    """
    return _Parser(tokenize(text)).parse()


def render(node: Node) -> str:
    """
    A query written back in full, so the operator sees what was understood.

    Args:
        node: A parsed query.

    Returns:
        ``error AND @unit:nginx AND NOT timeout`` for ``@unit:nginx error -timeout``.
    """
    if isinstance(node, Anything):
        return ""
    if isinstance(node, Term):
        return f'"{node.text}"' if re.search(r"\s", node.text) or node.text == "" else node.text
    if isinstance(node, Field):
        value = f'"{node.value}"' if re.search(r"\s", node.value) else node.value
        return f"@{node.name}:{value}"
    if isinstance(node, Not):
        inner = render(node.node)
        return f"NOT ({inner})" if isinstance(node.node, (And, Or)) else f"NOT {inner}"
    parts = [
        f"({render(child)})" if isinstance(child, Or) and isinstance(node, And) else render(child)
        for child in node.nodes
    ]
    return (" AND " if isinstance(node, And) else " OR ").join(parts)


@dataclass(frozen=True)
class Entry:
    """What a query can see of a journal entry."""

    unit: str
    priority: int
    pid: int | None
    message: str


#: Resolves an application's domain to the units it runs as.
AppUnits = Callable[[str], Sequence[str]]


def normalise_unit(unit: str) -> str:
    """Returns: The unit without ``.service`` and in lower case, so ``Nginx.service`` and ``nginx`` are one."""
    unit = unit.lower()
    return unit.removesuffix(".service")


def matches(node: Node, entry: Entry, app_units: AppUnits) -> bool:
    """
    Judge an entry against a query.

    Args:
        node: A parsed query.
        entry: The entry.
        app_units: Resolves ``@app:`` to unit names.

    Returns:
        Whether the entry is what the query asks for.
    """
    if isinstance(node, Anything):
        return True
    if isinstance(node, Term):
        return node.text.lower() in entry.message.lower()
    if isinstance(node, Not):
        return not matches(node.node, entry, app_units)
    if isinstance(node, And):
        return all(matches(child, entry, app_units) for child in node.nodes)
    if isinstance(node, Or):
        return any(matches(child, entry, app_units) for child in node.nodes)
    return _field_matches(node, entry, app_units)


def _field_matches(node: Field, entry: Entry, app_units: AppUnits) -> bool:
    if node.name == "unit":
        return fnmatch.fnmatchcase(normalise_unit(entry.unit), normalise_unit(node.value))
    if node.name == "app":
        return normalise_unit(entry.unit) in {
            normalise_unit(unit) for unit in app_units(node.value)
        }
    if node.name == "level":
        wanted = int(node.value) if node.value.isdigit() else LEVELS[node.value.lower()]
        return entry.priority <= wanted
    return entry.pid == int(node.value)


@dataclass(frozen=True)
class Pushdown:
    """
    What can be handed to ``journalctl`` to read less.

    Attributes:
        units: Unit names to read (``--unit``, which means any of them), or none to read all.
        priority: ``--priority`` level, or None.
    """

    units: tuple[str, ...] = ()
    priority: int | None = None


def pushdown(node: Node, app_units: AppUnits) -> Pushdown:
    """
    Find the part of a query ``journalctl`` can narrow by itself.

    Only clauses every matching entry must satisfy are used: the positive ``@unit``, ``@app`` and
    ``@level`` among the top-level conjuncts. An ``OR`` or a ``NOT`` is never pushed, and the whole
    query is judged again afterwards, so this can only make the read smaller, never wrong.

    Args:
        node: A parsed query.
        app_units: Resolves ``@app:`` to unit names.

    Returns:
        The units and level to read.
    """
    conjuncts = node.nodes if isinstance(node, And) else (node,)
    unit_sets: list[tuple[str, ...]] = []
    level: int | None = None
    for clause in conjuncts:
        if not isinstance(clause, Field):
            continue
        if clause.name == "unit" and not re.search(r"[*?\[]", clause.value):
            unit_sets.append((clause.value,))
        elif clause.name == "app":
            units = tuple(app_units(clause.value))
            if units:
                unit_sets.append(units)
        elif clause.name == "level":
            wanted = int(clause.value) if clause.value.isdigit() else LEVELS[clause.value.lower()]
            # Two level clauses must both hold: the stricter one is the narrower read.
            level = wanted if level is None else min(level, wanted)
    # Two unit clauses are an AND of two units: nothing logs as both, and reading either would hide that.
    return Pushdown(units=unit_sets[0] if len(unit_sets) == 1 else (), priority=level)
