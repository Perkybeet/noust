# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The variables a Compose file asks for, and which of them the stack has (checklist B-47).

A compose file interpolates ``${NAME}`` from the project's ``.env`` and from the environment of
the process that runs ``docker compose``. ``${NAME:-default}`` has its own default,
``${NAME:?message}`` refuses to run without a value, and a plain ``${NAME}`` that nothing sets
becomes an empty string with a warning that scrolls past in a build log. Until now Noust did not
read the file for any of this: a stack whose ``.env`` lacked ``DB_PASSWORD`` found out when
``docker compose up`` said so, minutes into a deploy.

This reads the document (never the text, so a commented ``# ${OLD}`` is not a variable), finds
every reference with the services that use it, and compares them with the ``.env`` the stack has:

- ``set``: the project's ``.env`` (or the server's environment) gives it a value;
- ``default``: nothing gives it a value and the file has a default;
- ``unset``: nothing gives it a value and the file has no default, so Compose will fill in an
  empty string;
- ``missing``: the file *requires* it (``${NAME:?message}``) and nothing gives it a value. Compose
  refuses to run, so a deploy or an update refuses first, before the build, naming it
  (:func:`require`).

Values are never part of the answer, only whether there is one. Compose's own variables
(``COMPOSE_*``) and the ones every process has (``HOME``, ``PATH``...) are not the stack's to set.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.exceptions import DeploymentError

#: Where a variable's value comes from, when it has one.
SOURCE_ENV_FILE = "env"
SOURCE_PROCESS = "process"

#: A variable's state, from fine to blocking.
SET = "set"
DEFAULT = "default"
UNSET = "unset"
MISSING = "missing"

#: Variables every process has or Compose defines itself: not for the stack's ``.env`` to give.
_BUILT_IN = frozenset({"HOME", "PATH", "PWD", "USER", "LOGNAME", "SHELL", "LANG", "TERM"})

#: What a variable's name is made of.
_NAME_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_NAME_CHARS = _NAME_START | frozenset("0123456789")

#: The longest value of a compose file walked: a pathological document is not followed forever.
_MAX_DEPTH = 40


@dataclass(frozen=True)
class Use:
    """
    One reference to a variable in a compose file.

    Attributes:
        name: The variable.
        operator: ``:-``, ``-``, ``:?``, ``?``, ``:+``, ``+``, or None for a plain ``${NAME}``
            or ``$NAME``.
        argument: What follows the operator (the default, the message).
        where: The service it is used in, or the top-level key (``volumes``, ``networks``...).
    """

    name: str
    operator: str | None
    argument: str
    where: str


def scan(text: str, where: str = "") -> Iterator[Use]:
    """
    Find the variable references in one string of a compose file.

    Compose's own grammar: ``$$`` is a literal ``$``, ``${NAME}``, ``$NAME``, and
    ``${NAME<op>word}`` where the word may itself hold references (a default that is another
    variable).

    Args:
        text: A scalar of the document.
        where: Where it was found, recorded in each :class:`Use`.

    Yields:
        Every reference, outer first, then the ones inside its default.
    """
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "$":
            index += 1
            continue
        following = text[index + 1] if index + 1 < length else ""
        if following == "$":
            index += 2
        elif following == "{":
            end = _closing(text, index + 2)
            if end is None:
                return
            yield from _braced(text[index + 2 : end], where)
            index = end + 1
        elif following in _NAME_START and following:
            stop = index + 1
            while stop < length and text[stop] in _NAME_CHARS:
                stop += 1
            yield Use(text[index + 1 : stop], None, "", where)
            index = stop
        else:
            index += 1


def _closing(text: str, start: int) -> int | None:
    """
    Find the brace that closes a ``${``.

    Args:
        text: The scalar.
        start: Just after the ``${``.

    Returns:
        The index of the matching ``}``; None when it never closes.
    """
    depth = 1
    index = start
    while index < len(text):
        if text.startswith("${", index):
            depth += 1
            index += 2
            continue
        if text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _braced(inner: str, where: str) -> Iterator[Use]:
    """
    Read what is inside ``${...}``.

    Args:
        inner: The text between the braces.
        where: Where it was found.

    Yields:
        The reference, then any inside its default.
    """
    stop = 0
    while stop < len(inner) and inner[stop] in _NAME_CHARS:
        stop += 1
    name = inner[:stop]
    if not name or name[0] not in _NAME_START:
        return
    rest = inner[stop:]
    for operator in (":-", ":?", ":+", "-", "?", "+"):
        if rest.startswith(operator):
            argument = rest[len(operator) :]
            yield Use(name, operator, argument, where)
            yield from scan(argument, where)
            return
    yield Use(name, None, "", where)


def walk(document: Any) -> Iterator[Use]:
    """
    Find every reference in a parsed compose document, with where it is.

    Args:
        document: What ``yaml.safe_load`` gave. A scalar's place is the service it belongs to
            (``services.<name>``), else the top-level key.

    Yields:
        Every reference.
    """
    if not isinstance(document, Mapping):
        return
    for key, value in document.items():
        top = str(key)
        if top == "services" and isinstance(value, Mapping):
            for service, body in value.items():
                yield from _strings(body, str(service), 0)
        else:
            yield from _strings(value, top, 0)


def _strings(node: Any, where: str, depth: int) -> Iterator[Use]:
    """
    Find the references in every string below a node.

    Args:
        node: A part of the document.
        where: The place to record.
        depth: How deep the walk is.

    Yields:
        Every reference.
    """
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, str):
        yield from scan(node, where)
    elif isinstance(node, Mapping):
        for key, value in node.items():
            if isinstance(key, str):
                yield from scan(key, where)
            yield from _strings(value, where, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item, where, depth + 1)


@dataclass(frozen=True)
class ComposeVariable:
    """
    A variable a compose file asks for, and what the stack has of it.

    Attributes:
        name: The variable.
        status: ``set``, ``default``, ``unset`` or ``missing``.
        required: Whether the file requires a value (``${NAME:?message}``).
        default: The default the file gives, when it does.
        message: What the file says when a required value is missing.
        source: Where the value comes from when ``status`` is ``set``: ``env`` or ``process``.
        used_in: The services (or top-level keys) that use it.
    """

    name: str
    status: str
    required: bool = False
    default: str | None = None
    message: str = ""
    source: str | None = None
    used_in: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The variable as JSON-ready data; never a value.
        """
        return {
            "name": self.name,
            "status": self.status,
            "required": self.required,
            "default": self.default,
            "message": self.message,
            "source": self.source,
            "used_in": list(self.used_in),
        }


@dataclass(frozen=True)
class VariableReport:
    """
    The variables of one compose file and which of them a stack has.

    Attributes:
        file: The compose file read, as a name.
        env_file: The ``.env`` the values were read from, when there is one.
        variables: Every variable, in the order the file first uses them.
    """

    file: str
    env_file: str | None = None
    variables: tuple[ComposeVariable, ...] = field(default_factory=tuple)

    @property
    def missing(self) -> tuple[ComposeVariable, ...]:
        """The variables the file requires and nothing gives."""
        return tuple(variable for variable in self.variables if variable.status == MISSING)

    @property
    def unset(self) -> tuple[ComposeVariable, ...]:
        """The variables with no value and no default, which Compose fills with nothing."""
        return tuple(variable for variable in self.variables if variable.status == UNSET)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The report as JSON-ready data.
        """
        return {
            "file": self.file,
            "env_file": self.env_file,
            "variables": [variable.to_dict() for variable in self.variables],
            "missing": [variable.name for variable in self.missing],
            "unset": [variable.name for variable in self.unset],
        }


def judge(
    document: Any,
    values: Mapping[str, str],
    *,
    file: str = "",
    env_file: str | None = None,
    process: Mapping[str, str] | None = None,
) -> VariableReport:
    """
    Compare what a compose document asks for with the values a stack has.

    Args:
        document: The parsed compose file.
        values: The stack's ``.env``.
        file: The compose file's name, for the report.
        env_file: The ``.env`` the values came from, for the report.
        process: The environment the stack is run with; the server's own when None.

    Returns:
        The report.
    """
    environment = os.environ if process is None else process
    uses: dict[str, list[Use]] = {}
    for use in walk(document):
        if use.name.startswith("COMPOSE_") or use.name in _BUILT_IN:
            continue
        uses.setdefault(use.name, []).append(use)

    found: list[ComposeVariable] = []
    for name, group in uses.items():
        required = [use for use in group if use.operator in (":?", "?")]
        defaults = [use for use in group if use.operator in (":-", "-")]
        # ``:?`` and ``:-`` treat an empty value as unset; ``?`` and ``-`` only an unset one.
        strict = any(use.operator == ":?" for use in required) or (
            not required and any(use.operator == ":-" for use in defaults)
        )
        source, value = _value_of(name, values, environment)
        present = value is not None and (value != "" or not strict)
        if present:
            status = SET
        elif required:
            status = MISSING
        elif defaults:
            status = DEFAULT
        else:
            status = UNSET
        places = tuple(dict.fromkeys(use.where for use in group if use.where))
        found.append(
            ComposeVariable(
                name=name,
                status=status,
                required=bool(required),
                default=defaults[0].argument if defaults else None,
                message=required[0].argument if required else "",
                source=source if present else None,
                used_in=places,
            )
        )
    return VariableReport(file=file, env_file=env_file, variables=tuple(found))


def _value_of(
    name: str, values: Mapping[str, str], process: Mapping[str, str]
) -> tuple[str | None, str | None]:
    """
    Find where a variable's value comes from, the way Compose looks: the process, then ``.env``.

    Args:
        name: The variable.
        values: The stack's ``.env``.
        process: The environment the stack is run with.

    Returns:
        ``(source, value)``; ``(None, None)`` when neither has it.
    """
    if name in process:
        return SOURCE_PROCESS, process[name]
    if name in values:
        return SOURCE_ENV_FILE, values[name]
    return None, None


def read_values(compose_path: Path, app_path: Path) -> tuple[dict[str, str], str | None]:
    """
    Read the ``.env`` a stack's compose file is interpolated from.

    Compose reads the ``.env`` beside the first compose file (the project directory); Noust writes
    the application's at its root. Both are read, the project directory's winning.

    Args:
        compose_path: The compose file.
        app_path: The application directory.

    Returns:
        The values and the file that gave them (the project directory's, else the root's);
        empty and None when there is no ``.env``.
    """
    # Imported here: the env manager builds on the deployers that import this module.
    from noust.deployers.helpers.env_manager import EnvManager

    manager = EnvManager(verbose=False)
    values: dict[str, str] = {}
    used: str | None = None
    for candidate in (app_path / ".env", compose_path.parent / ".env"):
        if candidate.is_file() and not candidate.is_symlink():
            values.update(manager.read_env_file(candidate))
            used = str(candidate)
    return values, used


def require(report: VariableReport, *, domain: str) -> None:
    """
    Refuse to go on while the compose file requires a value nothing gives.

    Args:
        report: What :func:`judge` made.
        domain: The application, for the command the details name.

    Raises:
        DeploymentError: A required variable has no value; names each with the file's own message.
    """
    missing = report.missing
    if not missing:
        return
    lines = [
        f"{variable.name}" + (f": {variable.message}" if variable.message else "")
        for variable in missing
    ]
    names = ", ".join(variable.name for variable in missing)
    raise DeploymentError(
        f"{report.file or 'The compose file'} requires {'a value' if len(missing) == 1 else 'values'} "
        f"for {names}",
        details="\n".join(lines)
        + f"\n\nSet {'it' if len(missing) == 1 else 'them'} with: noust env set {domain} "
        + " ".join(f"{variable.name}=<value>" for variable in missing)
        + "\nThe stack was not built and nothing that serves was touched.",
    )


def report_for(app: Any, *, runner: Any = None) -> VariableReport:
    """
    Say which variables the compose file of a deployed stack asks for and which it has.

    Args:
        app: The stack's row.
        runner: The runner the deployer is built with; the process-wide one when None.

    Returns:
        The report. Reads the file and the ``.env`` and runs nothing.

    Raises:
        DeploymentError: There is no compose file in the stack's directory, or it is not YAML.
    """
    # Imported here: the deployer builds on this module.
    import yaml

    from noust.deployers.docker_compose import stack_deployer

    deployer = stack_deployer(app, runner=runner)
    deployer._discover_compose_file()
    compose = deployer._compose_file_path()
    try:
        document = yaml.safe_load(compose.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DeploymentError(
            f"{compose.name} could not be read: {exc}",
            details="Fix the file in the repository; Noust reads it as it is on disk.",
        ) from exc
    values, env_file = read_values(compose, deployer.app_path)
    return judge(document, values, file=compose.name, env_file=env_file)
