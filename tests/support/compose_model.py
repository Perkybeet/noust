# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``docker compose config --format json`` prints for a stack, without Docker.

The guard over a Compose file judges the file as written and then the model Compose resolves it to
(a variable filled in, a short volume turned into a long one, a relative source made absolute). A
test that deploys a real document through the deployer has no Docker to ask. A runner that prints
nothing would leave the guard judging the file alone, which is a test of something else; this plays
Compose for the part of the model the guard reads, so such a test proves that the document passes
the guard with what Compose would make of it.

It does not pretend to resolve what it cannot: an ``include`` or an ``extends`` brings in text
that is not in the document, and printing a model without it would hide exactly what the guard is
there to see. Those raise :class:`UnsupportedComposeFeature`, so a document that starts to use one
fails loudly here and the double is taught first.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from noust.core.compose_yaml import load_compose
from noust.core.runner import CommandResult, FakeRunner
from noust.deployers.helpers.env_manager import EnvManager

__all__ = [
    "UnsupportedComposeFeature",
    "compose_resolving",
    "interpolate",
    "resolve_like_compose",
]

#: A variable's name.
_NAME = r"[_A-Za-z][_A-Za-z0-9]*"

#: ``NAME``, or ``NAME`` with an operator and the word that follows it, inside ``${...}``.
_BRACED = re.compile(rf"({_NAME})(?:(:?[-?+])(.*))?", re.DOTALL)

#: The sections of a document that hold things that run.
_RUNNABLE = ("services", "jobs")

#: The sections whose entries name a file on this server.
_FILES = ("secrets", "configs")


class UnsupportedComposeFeature(Exception):
    """A document uses something this double cannot print the model of."""


def _closing(text: str, start: int) -> int:
    """
    Find the brace that ends a ``${`` opened before ``start``.

    Args:
        text: The value.
        start: The index just after ``${``.

    Returns:
        The index of the closing brace, braces inside a default counted.

    Raises:
        ValueError: There is none.
    """
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"invalid interpolation format in {text!r}")


def _braced(inner: str, variables: Mapping[str, str]) -> str:
    """
    Resolve what is inside ``${...}``.

    Args:
        inner: ``NAME`` or ``NAME<operator>word``.
        variables: What the variables are.

    Returns:
        The value, which may be empty.

    Raises:
        ValueError: The reference is malformed, or it requires a value nothing gives.
    """
    parsed = _BRACED.fullmatch(inner)
    if parsed is None:
        raise ValueError(f"invalid interpolation format for ${{{inner}}}")
    name, operator, word = parsed.group(1), parsed.group(2), parsed.group(3) or ""
    value = variables.get(name)
    if operator is None:
        return value or ""
    # With the colon an empty value counts as unset; without it only a missing one does.
    unset = value is None or (operator.startswith(":") and value == "")
    kind = operator[-1]
    if kind == "-":
        return interpolate(word, variables) if unset else value or ""
    if kind == "+":
        return "" if unset else interpolate(word, variables)
    if unset:
        raise ValueError(
            f"required variable {name} is missing a value: {interpolate(word, variables)}"
        )
    return value or ""


def interpolate(text: str, variables: Mapping[str, str]) -> str:
    """
    Fill in the variables of one value the way Compose does.

    Args:
        text: A scalar of the document.
        variables: What the variables are.

    Returns:
        The value with ``$$`` a dollar sign, ``$NAME`` and ``${NAME}`` their values (empty when
        nothing gives one) and the ``-``, ``?`` and ``+`` forms with and without a colon.

    Raises:
        ValueError: A ``$`` that starts no reference, an unterminated brace, or a required
            variable nothing gives: Compose stops on each of them.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "$":
            out.append(char)
            index += 1
            continue
        following = text[index + 1 : index + 2]
        if following == "$":
            out.append("$")
            index += 2
        elif following == "{":
            end = _closing(text, index + 2)
            out.append(_braced(text[index + 2 : end], variables))
            index = end + 1
        else:
            named = re.compile(_NAME).match(text, index + 1)
            if named is None:
                raise ValueError(f"invalid interpolation format in {text!r}")
            out.append(variables.get(named.group(0), ""))
            index = named.end()
    return "".join(out)


def _filled(node: Any, variables: Mapping[str, str]) -> Any:
    """
    Fill in the variables of every string of a document.

    Args:
        node: A document, or a part of one.
        variables: What the variables are.

    Returns:
        The same shape, its keys untouched, as Compose leaves them.
    """
    if isinstance(node, str):
        return interpolate(node, variables)
    if isinstance(node, Mapping):
        return {key: _filled(value, variables) for key, value in node.items()}
    if isinstance(node, list):
        return [_filled(value, variables) for value in node]
    return node


def _absolute(source: str, directory: Path) -> str:
    """
    Make a host path the absolute one Compose prints.

    Args:
        source: A bind mount's source or a secret's file, as written.
        directory: The project directory, which a relative path is relative to.

    Returns:
        The path with ``~`` expanded and ``.`` and ``..`` collapsed.
    """
    expanded = os.path.expanduser(source)
    joined = expanded if expanded.startswith("/") else posixpath.join(str(directory), expanded)
    return posixpath.normpath(joined)


def _long_volume(volume: Any, directory: Path) -> Any:
    """
    Print a volume the way ``config --format json`` does.

    Args:
        volume: A short entry (``./data:/data:ro``) or a long one.
        directory: The project directory.

    Returns:
        A long entry: a bind mount with an absolute source, or a volume.
    """
    if isinstance(volume, Mapping):
        entry = dict(volume)
        if entry.get("type") == "bind" and isinstance(entry.get("source"), str):
            entry["source"] = _absolute(entry["source"], directory)
        return entry
    parts = str(volume).split(":")
    if len(parts) == 1:
        return {"type": "volume", "target": parts[0]}
    source, target = parts[0], parts[1]
    printed: dict[str, Any]
    if source.startswith(("/", ".", "~")):
        printed = {"type": "bind", "source": _absolute(source, directory), "target": target}
    else:
        printed = {"type": "volume", "source": source, "target": target}
    if "ro" in parts[2:]:
        printed["read_only"] = True
    return printed


def _refuse_what_brings_text_in(document: Mapping[str, Any]) -> None:
    """
    Raise for an ``include`` or an ``extends``.

    Args:
        document: The document.

    Raises:
        UnsupportedComposeFeature: The document brings in text that is not in it.
    """
    if document.get("include"):
        raise UnsupportedComposeFeature("include: what it brings in is not in the document")
    for section in _RUNNABLE:
        entries = document.get(section)
        for name, service in entries.items() if isinstance(entries, Mapping) else ():
            if isinstance(service, Mapping) and service.get("extends"):
                raise UnsupportedComposeFeature(f"{section}.{name} extends another definition")


def resolve_like_compose(
    document: Mapping[str, Any], directory: Path, variables: Mapping[str, str]
) -> dict[str, Any]:
    """
    Print the model Compose resolves a document to, for what the guard reads.

    Args:
        document: The compose file, parsed.
        directory: The project directory (where the file is).
        variables: What ``${NAME}`` is: the ``.env`` and the environment of the process.

    Returns:
        The document with its variables filled, the project's name, every volume a long entry
        with an absolute source and the files of secrets and configs absolute. Every profile is
        on, as ``--profile "*"`` asks, which is every service the document holds.

    Raises:
        UnsupportedComposeFeature: The document includes or extends another.
        ValueError: The document cannot be interpolated, as Compose would stop.
    """
    _refuse_what_brings_text_in(document)
    model: dict[str, Any] = {"name": directory.name, **_filled(document, variables)}
    for section in _RUNNABLE:
        for service in (model.get(section) or {}).values():
            if isinstance(service, dict) and isinstance(service.get("volumes"), list):
                service["volumes"] = [_long_volume(v, directory) for v in service["volumes"]]
    for section in _FILES:
        for entry in (model.get(section) or {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("file"), str):
                entry["file"] = _absolute(entry["file"], directory)
    return model


def compose_resolving(directory: Path, variables: Mapping[str, str]) -> FakeRunner:
    """
    A runner whose ``docker compose`` prints the model of the stack in a directory.

    The file is read when the command is asked, as Compose does, so a test may write it after
    making the runner. The variables are the stack's ``.env`` and then the ones given, which win.

    Args:
        directory: Where the stack's compose file and ``.env`` are.
        variables: The variables beyond the ``.env``.

    Returns:
        A runner that answers every ``docker compose`` command with the model.
    """
    return _ResolvingRunner(directory, variables)


class _ResolvingRunner(FakeRunner):
    """A fake runner that prints the model of one stack for ``docker compose``."""

    def __init__(self, directory: Path, variables: Mapping[str, str]) -> None:
        super().__init__()
        self._directory = directory
        self._variables = dict(variables)

    def _lookup(
        self,
        argv: Sequence[str],
        user: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        result = super()._lookup(argv, user, env)
        if tuple(argv[:2]) != ("docker", "compose") or "config" not in argv:
            return result
        text = (self._directory / "docker-compose.yml").read_text(encoding="utf-8")
        values: dict[str, str] = {}
        dotenv = self._directory / ".env"
        if dotenv.is_file():
            values.update(EnvManager(verbose=False).read_env_file(dotenv))
        values.update(self._variables)
        model = resolve_like_compose(load_compose(text), self._directory, values)
        return replace(result, stdout=json.dumps(model))
