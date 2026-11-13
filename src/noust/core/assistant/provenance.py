# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a value of a call the assistant prepares comes from (item 171).

A model that reads a log, a README or a commit message reads text that anybody who can write to the
application wrote, and may be misled by it. What it can do with that is bounded by the server (the
token, the policy, the floor); what is added here is the one thing the server can still tell: a
value in a call that is a host, an address, a URL, an email, a command or the value of a variable,
and that the person did not write and Noust does not know, did not come from the person. It came
from text somebody else wrote, or the model made it up. Either way the call is not made on its own
whatever the policy says, and the person is shown the value, highlighted, with where it was found.

The test is about values and not about words: a branch called ``main``, a flag, a port, a release
identifier and a file name are not values to doubt. Shape decides what is one: a name with a
domain's shape, an address, a URL, an email, a string with the marks of a shell command, or a field
whose name says it holds a command or the value of a variable.
"""

from __future__ import annotations

import ipaddress
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = [
    "KIND_ADDRESS",
    "KIND_COMMAND",
    "KIND_EMAIL",
    "KIND_HOST",
    "KIND_URL",
    "KIND_VALUE",
    "Foreign",
    "Origin",
    "Provenance",
    "known_names",
]

KIND_HOST = "host"
KIND_ADDRESS = "address"
KIND_URL = "url"
KIND_EMAIL = "email"
KIND_COMMAND = "command"
KIND_VALUE = "value"

#: Fields whose name says they hold something a machine will run.
COMMAND_KEYS = frozenset(
    {
        "command",
        "cmd",
        "script",
        "exec",
        "shell",
        "entrypoint",
        "hook",
        "hooks",
        "pre_deploy",
        "post_deploy",
        "start_command",
        "build_command",
        "install_command",
    }
)

#: Fields whose name says they hold a value somebody has to have chosen.
VALUE_KEYS = frozenset(
    {"value", "values", "secret", "password", "token", "env", "variables", "environment", "content"}
)

#: What a file name ends in, which is not the end of a host name.
_FILE_ENDINGS = frozenset(
    "js ts tsx jsx mjs cjs json yaml yml py pyc md txt sh bash env conf cfg ini log html htm css php rb go rs "
    "toml lock xml sql service socket timer pem key crt csr png jpg jpeg gif svg ico pdf zip gz tar tgz bz2 "
    "xz so dll exe bin jar war java kt c h cpp hpp cs lua pl ex exs erl swift dart vue svelte map "
    "csv tsv gradle properties dockerfile example sample bak tmp swp lck pid sock".split()
)

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>`]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
_IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:.])")
_HOST = re.compile(
    r"(?<![\w.\-@/])(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}(?![\w\-])"
)
_SHELL = re.compile(r"(\|\||&&|\||;\s|`|\$\(|\s>\s|>>|\s<\s)")
_SPACE = re.compile(r"\s+")

#: The most characters of an argument examined: a call's value longer than this is judged on its start.
_MAX_VALUE = 20_000
#: The deepest nesting looked into.
_MAX_DEPTH = 8


@dataclass(frozen=True)
class Origin:
    """
    Where a foreign value was found.

    Attributes:
        source: The identifier of the text from outside it was found in, or None when it was found
            nowhere (the model made it up, or took it from somewhere it did not show).
        line: The line of that text.
    """

    source: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class Foreign:
    """
    A value of a call that does not come from the person or from Noust.

    Attributes:
        path: Where in the arguments it is (``body.hooks.post_deploy[0]``).
        value: The value (the whole string it is part of).
        kind: ``host``, ``address``, ``url``, ``email``, ``command`` or ``value``.
        origin: Where it was found.
    """

    path: str
    value: str
    kind: str
    origin: Origin


def _norm(text: str) -> str:
    """
    Args:
        text: Any text.

    Returns:
        It without letter case and with every run of white space one space.
    """
    return _SPACE.sub(" ", text.strip()).casefold()


def known_names(store: NoustStore | None = None) -> Callable[[str], bool]:
    """
    Read the names Noust itself knows, once, to ask about them many times.

    Args:
        store: The store; the process-wide one when None.

    Returns:
        A function that says whether a name is an application's domain, a domain an application
        answers on, or the name of a server of the fleet.
    """
    if store is None:
        from noust.core.store import get_store

        store = get_store()
    names: set[str] = set()
    with store._transaction() as cursor:
        for statement in (
            "SELECT domain FROM apps",
            "SELECT domain FROM domains",
            "SELECT name FROM nodes",
        ):
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                # A store being upgraded may lack a table: a shorter list of names, never a failure.
                continue
            names.update(str(row[0]).casefold() for row in cursor.fetchall() if row[0])

    def known(value: str) -> bool:
        return value.strip().casefold() in names

    return known


class Provenance:
    """What the person wrote, what Noust knows and what came from outside, for judging a call."""

    def __init__(
        self,
        said: Iterable[str] = (),
        *,
        known: Callable[[str], bool] | None = None,
        outside: Mapping[str, Sequence[tuple[int, str]]] | None = None,
    ) -> None:
        """
        Args:
            said: What the person wrote, as they wrote it (not as the model was sent it).
            known: Says whether a name is one Noust knows (:func:`known_names`).
            outside: Texts from outside by identifier, each as ``(line number, line)``.
        """
        self._said = "\n".join(_norm(text) for text in said)
        self._known = known or (lambda value: False)
        self._outside: dict[str, list[tuple[int, str]]] = {
            key: list(lines) for key, lines in (outside or {}).items()
        }

    def add_said(self, text: str) -> None:
        """
        Args:
            text: Something else the person wrote.
        """
        self._said += "\n" + _norm(text)

    def add_outside(self, source: str, lines: Sequence[tuple[int, str]]) -> None:
        """
        Args:
            source: An identifier for a text from outside.
            lines: Its lines with their numbers.
        """
        self._outside.setdefault(source, []).extend(lines)

    # -- judging -----------------------------------------------------------------------------------

    def foreign(self, arguments: Mapping[str, Any]) -> list[Foreign]:
        """
        Find the values of a call that are not the person's or Noust's.

        Args:
            arguments: The arguments of a call, nested as they are.

        Returns:
            Each value that is, with its kind and where it was found.
        """
        found: list[Foreign] = []
        self._walk(arguments, "", None, found, 0)
        return found

    def _walk(
        self, value: Any, path: str, hint: str | None, found: list[Foreign], depth: int
    ) -> None:
        """
        Args:
            value: A part of the arguments.
            path: Where it is.
            hint: What the nearest field that says what it holds says (``command`` or ``value``): a
                variable's value is a value whatever the variable is called.
            found: Where what is foreign is collected.
            depth: How deep it is.
        """
        if depth > _MAX_DEPTH:
            return
        if isinstance(value, Mapping):
            for name, item in value.items():
                label = str(name)
                folded = label.casefold()
                held = (
                    KIND_COMMAND
                    if folded in COMMAND_KEYS
                    else KIND_VALUE
                    if folded in VALUE_KEYS
                    else hint
                )
                self._walk(item, f"{path}.{label}" if path else label, held, found, depth + 1)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                self._walk(item, f"{path}[{index}]", hint, found, depth + 1)
        elif isinstance(value, str):
            kind = self._doubt(value[:_MAX_VALUE], hint)
            if kind is not None:
                found.append(Foreign(path, value, kind, self._origin(value)))

    def _doubt(self, text: str, hint: str | None) -> str | None:
        """
        Args:
            text: A value of a call.
            hint: What the field it is in says it holds, if it says.

        Returns:
            Its kind when it is a value to doubt and is not theirs; None otherwise.
        """
        stripped = text.strip()
        if not stripped:
            return None
        norm = _norm(stripped)
        if norm in self._said or self._known(stripped):
            return None
        if hint == KIND_COMMAND or _SHELL.search(stripped):
            return KIND_COMMAND
        if hint == KIND_VALUE:
            return KIND_VALUE
        for kind, token in _shapes(stripped):
            if not self._ours(token, kind):
                return kind
        return None

    def _ours(self, token: str, kind: str) -> bool:
        """
        Args:
            token: A host, an address, a URL or an email found in a value.
            kind: What it is.

        Returns:
            Whether the person wrote it or Noust knows it.
        """
        if _norm(token) in self._said or self._known(token):
            return True
        host = _host_of(token, kind)
        return bool(host) and (_norm(host) in self._said or self._known(host))

    # -- where it came from ------------------------------------------------------------------------

    def _origin(self, value: str) -> Origin:
        """
        Args:
            value: A foreign value.

        Returns:
            The first text from outside that holds it, and its line.
        """
        needle = _norm(value)
        for source, lines in self._outside.items():
            for number, line in lines:
                if needle in _norm(line):
                    return Origin(source, number)
        for source, lines in self._outside.items():
            probe = _first_token(value)
            if probe and any(probe in _norm(line) for _number, line in lines):
                number = next(n for n, line in lines if probe in _norm(line))
                return Origin(source, number)
        return Origin()


def _first_token(value: str) -> str:
    """
    Args:
        value: A foreign value.

    Returns:
        The first host, address, URL or email in it, as a normal text; the value's longest word
        when it has none.
    """
    for _kind, token in _shapes(value):
        return _norm(token)
    words = sorted(value.split(), key=len, reverse=True)
    return _norm(words[0]) if words and len(words[0]) >= 6 else ""


def _host_of(token: str, kind: str) -> str:
    """
    Args:
        token: A URL, an email, an address or a host.
        kind: Which.

    Returns:
        The name or address it points at.
    """
    if kind == KIND_URL:
        try:
            return urlsplit(token).hostname or ""
        except ValueError:
            return ""
    if kind == KIND_EMAIL:
        return token.rpartition("@")[2]
    return token


def _shapes(text: str) -> list[tuple[str, str]]:
    """
    Args:
        text: A value of a call.

    Returns:
        ``(kind, token)`` for each URL, email, address and host name in it, in that order of
        precedence and without counting one inside another.
    """
    found: list[tuple[str, str]] = []
    rest = text
    for kind, pattern in ((KIND_URL, _URL), (KIND_EMAIL, _EMAIL)):
        for match in pattern.finditer(rest):
            found.append((kind, match.group(0).rstrip(".,;)")))
        rest = pattern.sub(" ", rest)
    for pattern in (_IPV4, _IPV6):
        for match in pattern.finditer(rest):
            candidate = match.group(0)
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if address.is_loopback or address.is_unspecified:
                continue
            found.append((KIND_ADDRESS, candidate))
        rest = pattern.sub(" ", rest)
    for match in _HOST.finditer(rest):
        name = match.group(0)
        if name.rpartition(".")[2].casefold() in _FILE_ENDINGS:
            continue
        found.append((KIND_HOST, name))
    return found
