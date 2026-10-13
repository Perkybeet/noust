# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a unit launches its application: the command, the directory and the ``PATH``.

An application's build block can change all three after the unit was written
(a new ``start``, another ``base_directory``, a pinned runtime), and an update
has to carry the change to the unit without a redeploy. The unit is edited
rather than rendered again, for the reason
:func:`noust.managers.service_manager.with_resource_limits` gives: an operator
may have edited the rest of it by hand.

:func:`systemd_command` is the other direction: a command that is an argument
vector (never a shell line) becomes the ``ExecStart=`` text systemd parses back
to the same vector.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from noust.core.exceptions import ServiceError
from noust.validators.environment import (
    escape_systemd_value,
    validate_env_value,
    validate_unit_value,
)

#: An argument systemd would split or reinterpret unless it is quoted: space,
#: a quote, a backslash, a semicolon (a command separator when it stands
#: alone) and the empty string.
_NEEDS_QUOTES = re.compile(r"""[\s"'\\;]|^$""")

#: The ``PATH`` line of a unit: ``Environment="PATH=..."``.
_PATH_LINE = re.compile(r'^\s*Environment=\s*"PATH=')


def systemd_command(argv: Sequence[str]) -> str:
    """
    Write an argument vector as the text of an ``ExecStart=``.

    systemd expands ``%`` specifiers and ``$VAR``/``${VAR}`` in a command
    line: the first is doubled (a literal ``%`` in an argument stays literal),
    the second is kept, because ``${PORT}`` in a start command is how an
    application is told its port.

    Args:
        argv: The program and its arguments, the program absolute.

    Returns:
        The command line.

    Raises:
        ValidationError: An argument holds a control character, which would end
            the directive and start another.
    """
    words = []
    for argument in argv:
        word = validate_unit_value(argument, field="ExecStart").replace("%", "%%")
        if _NEEDS_QUOTES.search(word):
            word = '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'
        words.append(word)
    return " ".join(words)


def with_launch(unit: str, *, command: str, working_directory: str, path: str | None) -> str:
    """
    Make an existing unit launch its application as given, changing nothing else.

    Args:
        unit: The unit file.
        command: The ``ExecStart=`` text.
        working_directory: The ``WorkingDirectory=``.
        path: The value of ``PATH`` the unit sets, or None for the system's
            own (the line, if there is one, is removed).

    Returns:
        The unit.

    Raises:
        ServiceError: The unit has no ``[Service]`` section.
        ValidationError: A value that cannot be written into a unit.
    """
    command = validate_unit_value(command, field="ExecStart")
    working_directory = validate_unit_value(working_directory, field="WorkingDirectory")
    path_line = None
    if path is not None:
        path_line = (
            f'Environment="PATH={escape_systemd_value(validate_env_value(path, name="PATH"))}"'
        )

    lines = unit.split("\n")
    section = ""
    out: list[str] = []
    seen_exec = seen_dir = seen_path = False
    last_environment = last_working = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            out.append(line)
            continue
        if section == "[Service]":
            if stripped.startswith("ExecStart="):
                if not seen_exec:
                    out.append(f"ExecStart={command}")
                    seen_exec = True
                continue
            if stripped.startswith("WorkingDirectory="):
                if not seen_dir:
                    out.append(f"WorkingDirectory={working_directory}")
                    last_working = len(out) - 1
                    seen_dir = True
                continue
            if _PATH_LINE.match(line):
                if path_line is not None and not seen_path:
                    out.append(path_line)
                    last_environment = len(out) - 1
                    seen_path = True
                continue
            if stripped.startswith("Environment="):
                out.append(line)
                last_environment = len(out) - 1
                continue
        out.append(line)

    if section == "" and not any(line.strip() == "[Service]" for line in lines):
        raise ServiceError(
            "The unit has no [Service] section to set its command in",
            details="Check the unit with 'systemctl cat <name>'; Noust writes one in every unit.",
        )
    if path_line is not None and not seen_path:
        anchor = last_environment if last_environment is not None else last_working
        if anchor is None:
            raise ServiceError(
                "The unit has no WorkingDirectory to put the PATH after",
                details="Check the unit with 'systemctl cat <name>'.",
            )
        out.insert(anchor + 1, path_line)
    if not seen_exec or not seen_dir:
        raise ServiceError(
            "The unit has no ExecStart or WorkingDirectory to change",
            details="Check the unit with 'systemctl cat <name>'; Noust writes both in every unit.",
        )
    return "\n".join(out)


def release_env_line(directory: str, *, instance: bool = False) -> str:
    """
    Write the ``EnvironmentFile=`` line that loads a release's own variables.

    Args:
        directory: The directory the file is in: ``current`` for an application's own
            unit, the instances' directory (``colors``) for a template.
        instance: The line of a template, where each instance (``%i``) loads
            the file of the release it runs.

    Returns:
        The line, without a newline.
    """
    from noust.core.variables.predefined import RELEASE_ENV_NAME

    where = directory.replace("%", "%%")
    return (
        f"EnvironmentFile=-{where}/%i/{RELEASE_ENV_NAME}"
        if instance
        else f"EnvironmentFile=-{where}/{RELEASE_ENV_NAME}"
    )


def with_release_env(unit: str, line: str) -> str:
    """
    Make an existing unit load a release's own variables, changing nothing else.

    The line goes after the unit's other environment files, so what Noust says about
    the deployment wins over the ``.env``, and before the file that gives an
    instance its port (``<instance>.env``), which must stay last.

    Args:
        unit: The unit file.
        line: What :func:`release_env_line` wrote.

    Returns:
        The unit with the line; the same text when it has it already.

    Raises:
        ServiceError: The unit has no ``[Service]`` section.
    """
    lines = unit.split("\n")
    if any(candidate.strip() == line for candidate in lines):
        return unit
    section = ""
    anchor: int | None = None
    port_file: int | None = None
    last_environment: int | None = None
    working: int | None = None
    for index, text in enumerate(lines):
        stripped = text.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section != "[Service]":
            continue
        if stripped.startswith("EnvironmentFile="):
            if stripped.endswith("/%i.env"):
                port_file = index
            else:
                anchor = index
        elif stripped.startswith("Environment="):
            last_environment = index
        elif stripped.startswith("WorkingDirectory="):
            working = index
    if section == "" and not any(text.strip() == "[Service]" for text in lines):
        raise ServiceError(
            "The unit has no [Service] section to set its environment in",
            details="Check the unit with 'systemctl cat <name>'; Noust writes one in every unit.",
        )
    if port_file is not None:
        at = port_file
    elif anchor is not None:
        at = anchor + 1
    elif last_environment is not None:
        at = last_environment + 1
    elif working is not None:
        at = working + 1
    else:
        raise ServiceError(
            "The unit has no WorkingDirectory to put its environment after",
            details="Check the unit with 'systemctl cat <name>'.",
        )
    lines.insert(at, line)
    return "\n".join(lines)


__all__ = ["release_env_line", "systemd_command", "with_launch", "with_release_env"]
