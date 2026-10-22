# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Process types a repository declares: ``web``, ``release``, workers (checklist A-52).

Heroku's Procfile and Piku's ``ENV``/``Procfile`` name what a project runs in one place::

    web: bundle exec puma -p $PORT
    release: bundle exec rails db:migrate
    worker: bundle exec sidekiq

``noust.yaml`` says the same under ``processes:``, as text or as a list of arguments::

    processes:
      web: bundle exec puma -b tcp://127.0.0.1:${PORT}
      release: [bundle, exec, rails, db:migrate]
      worker: bundle exec sidekiq

This module is the one reader of both (rule 3). A ``processes:`` block wins over a ``Procfile``
whole, the way the operator's hooks win over the repository's: nothing is merged. A command is
an argument vector, never a shell line, so a Procfile line that needs a shell (``&&``, a pipe, a
redirection, a substitution) is refused with what to do instead rather than run as arguments that
mean something else; leading ``NAME=value`` words, which Heroku's shell takes as environment, run
the program through ``env``.

What each type does:

- ``web`` is the unit's command when the build block gives no ``start`` of its own.
- ``release`` is a ``pre_deploy`` hook when the repository declares no hooks (a migration:
  deployment stops there when it fails, before any traffic moves).
- Every other type (``worker``, ``clock``, ``scheduler``) is a process of the application that runs
  beside ``web`` (:meth:`noust.managers.app_processes.ProcessManager.declare`); this module only
  reads them.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from noust.core.exceptions import ValidationError

#: The file a Procfile is read from, at the root of the tree.
PROCFILE = "Procfile"

#: The largest Procfile read; a real one is a few lines.
MAX_PROCFILE_BYTES = 16 * 1024

#: How many process types one project declares.
MAX_PROCESSES = 16

#: A process type's name: lowercase, as Heroku requires.
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

#: ``NAME=value`` at the front of a command: an environment assignment in a shell.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: The characters of a word a shell reads as an operator (``&&``, ``|``, ``;``, ``>``, ``(``).
_OPERATOR_CHARS = frozenset("();<>|&")

#: What the process types are for.
WEB = "web"
RELEASE = "release"

ProcessSource = Literal["noust.yaml", "Procfile", "none"]


@dataclass(frozen=True)
class ProcessType:
    """
    One kind of process a project runs.

    Attributes:
        name: ``web``, ``release``, ``worker``...
        argv: The program and its arguments; never given to a shell.
        source: Where it was declared.
    """

    name: str
    argv: tuple[str, ...]
    source: ProcessSource

    @property
    def command(self) -> str:
        """The argv as one line, quoted the way a shell would need it."""
        return shlex.join(self.argv)

    @property
    def started_by_noust(self) -> bool:
        """Whether the deployment itself starts or runs it: ``web`` and ``release``, not a process beside them."""
        return self.name in (WEB, RELEASE)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            Its fields; ``argv`` as a list.
        """
        return {
            "name": self.name,
            "argv": list(self.argv),
            "command": self.command,
            "source": self.source,
            "started_by_noust": self.started_by_noust,
        }


@dataclass(frozen=True)
class ProcessSet:
    """
    The process types of one tree, from one place.

    Attributes:
        processes: In the order they were declared.
        source: ``noust.yaml``, ``Procfile`` or ``none``.
        problem: Why a ``Procfile`` that exists was not used; empty otherwise. A Procfile came
            with the repository long before Noust read one, and a deployment that worked
            without it must not stop working because of what it says, so it is reported.
    """

    processes: tuple[ProcessType, ...] = ()
    source: ProcessSource = "none"
    problem: str = ""

    def named(self, name: str) -> ProcessType | None:
        """
        Args:
            name: A process type.

        Returns:
            It, or None when the project does not declare it.
        """
        return next((process for process in self.processes if process.name == name), None)

    @property
    def declared(self) -> bool:
        """Whether the project declares any process type."""
        return bool(self.processes)

    def __iter__(self) -> Iterator[ProcessType]:
        return iter(self.processes)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            The source and every process type.
        """
        return {
            "source": self.source,
            "problem": self.problem,
            "processes": [process.to_dict() for process in self.processes],
        }


def _command(value: object, where: str) -> tuple[str, ...]:
    """
    Turn a declared command into an argument vector, refusing what needs a shell.

    Args:
        value: Text, split as a shell would split it, or a list of arguments.
        where: The field to name in a refusal.

    Returns:
        The argv; leading ``NAME=value`` words become ``env NAME=value ... program``.

    Raises:
        ValidationError: Empty, not text, unbalanced quotes, or a shell operator.
    """
    from_text = isinstance(value, str)
    if isinstance(value, str):
        try:
            # Punctuation is split off the way a shell splits it, so ``./start;`` is a program
            # and an operator, not one odd word; what is quoted stays whole.
            lexer = shlex.shlex(value, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            words = list(lexer)
        except ValueError as exc:
            raise ValidationError(
                f"{where} cannot be split into a command: {exc}",
                details="Close every quote. A process runs one program, never a shell.",
                field=where,
            ) from exc
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        words = list(value)
    else:
        raise ValidationError(
            f"{where} must be the command to run",
            details="Write it as text (web: ./bin/server) or as a list of arguments.",
            field=where,
        )
    if not words or not words[0]:
        raise ValidationError(f"{where} is empty", details="Name the program to run.", field=where)
    if any("\x00" in word for word in words):
        raise ValidationError(f"{where} holds a NUL byte", details="Remove it.", field=where)
    # A list is an argument vector already: what it holds is what the program is given.
    offending = next(
        (
            word
            for word in (words if from_text else ())
            if set(word) <= _OPERATOR_CHARS or "`" in word
        ),
        None,
    )
    if offending is not None:
        raise ValidationError(
            f"{where} needs a shell ({offending!r}), and Noust never runs one",
            details="Put the commands in a script of the repository and run that "
            f"({where.rsplit('.', 1)[-1]}: ./scripts/run.sh). Here a command is an argument "
            f"vector, so {offending!r} would be handed to the program as an argument.",
            field=where,
        )
    assignments = 0
    while assignments < len(words) and _ASSIGNMENT.match(words[assignments]):
        assignments += 1
    if assignments == len(words):
        raise ValidationError(
            f"{where} sets variables and runs nothing",
            details="Name the program after the NAME=value words.",
            field=where,
        )
    if assignments:
        return ("env", *words)
    return tuple(words)


def _name(value: object, where: str) -> str:
    """
    Args:
        value: A process type's name.
        where: The field to name in a refusal.

    Returns:
        The name.

    Raises:
        ValidationError: Not lowercase letters, digits, ``_`` and ``-``, or too long.
    """
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValidationError(
            f"{where}: {value!r} is not a process name",
            details="Use lowercase letters, digits, '_' and '-', starting with a letter "
            "(web, release, worker).",
            field=where,
        )
    return value


def parse_procfile(text: str) -> ProcessSet:
    """
    Read a Procfile.

    Args:
        text: Its content: ``name: command`` lines, ``#`` comments, blank lines.

    Returns:
        The process types, in file order.

    Raises:
        ValidationError: A line is not ``name: command``, a name is repeated, or
            a command needs a shell.
    """
    found: list[ProcessType] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, command = line.partition(":")
        where = f"Procfile line {number}"
        if not separator or not command.strip():
            raise ValidationError(
                f"{where} is not 'name: command'",
                details="Write each process as web: ./bin/server, one per line.",
                field="Procfile",
            )
        name = _name(name.strip(), where)
        if any(process.name == name for process in found):
            raise ValidationError(
                f"{where}: {name} is declared twice", details="Keep one.", field="Procfile"
            )
        argv = _command(command.strip(), f"{where} ({name})")
        found.append(ProcessType(name, argv, "Procfile"))
    _check_count(found, "Procfile")
    return ProcessSet(tuple(found), "Procfile" if found else "none")


def parse_processes_block(value: object) -> ProcessSet:
    """
    Read the ``processes:`` block of ``noust.yaml``.

    Args:
        value: What the key holds: a mapping of name to command (text or a list of
            arguments), or to ``{run: command}``.

    Returns:
        The process types, in the order written.

    Raises:
        ValidationError: Not a mapping, a bad name, or a command that needs a shell.
    """
    if value is None:
        return ProcessSet()
    if not isinstance(value, Mapping):
        raise ValidationError(
            "processes must be a mapping of process names to commands",
            details="Write it as processes: {web: ./bin/server, worker: ./bin/worker}.",
            field="processes",
        )
    found: list[ProcessType] = []
    for raw_name, entry in value.items():
        name = _name(raw_name, f"processes.{raw_name}")
        where = f"processes.{name}"
        if isinstance(entry, Mapping):
            for key in entry:
                if key != "run":
                    raise ValidationError(
                        f"Unknown key {key!r} in {where}",
                        details="Accepted here: run.",
                        field=f"{where}.{key}",
                    )
            entry = entry.get("run")
        found.append(ProcessType(name, _command(entry, where), "noust.yaml"))
    _check_count(found, "noust.yaml")
    return ProcessSet(tuple(found), "noust.yaml" if found else "none")


def _check_count(found: list[ProcessType], where: str) -> None:
    """
    Args:
        found: What was declared.
        where: The file, for the refusal.

    Raises:
        ValidationError: More than :data:`MAX_PROCESSES`.
    """
    if len(found) > MAX_PROCESSES:
        raise ValidationError(
            f"{where} declares more than {MAX_PROCESSES} process types",
            details="Keep the ones that run; a project of this size has too many.",
            field=where,
        )


def read_procfile(root: Path) -> ProcessSet:
    """
    Read the ``Procfile`` at the root of a tree.

    Args:
        root: The tree.

    Returns:
        The process types; none when there is no file, or none when the file cannot be used,
        with :attr:`ProcessSet.problem` saying why (a link, too large, a line that needs a shell).
    """
    try:
        return _read_procfile(root)
    except ValidationError as exc:
        return ProcessSet(problem=f"{exc.message}. {exc.details}".strip())


def _read_procfile(root: Path) -> ProcessSet:
    """
    Read the ``Procfile`` at the root of a tree, strictly.

    Args:
        root: The tree.

    Returns:
        The process types; none when there is no file.

    Raises:
        ValidationError: The file is a link, too large or not valid.
    """
    path = root / PROCFILE
    if not path.is_symlink() and not path.exists():
        return ProcessSet()
    if path.is_symlink() or not path.is_file():
        raise ValidationError(
            "Procfile is a link or not a regular file, so it is not read",
            details="Commit Procfile as a regular file at the root of the repository.",
            field="Procfile",
        )
    if path.stat().st_size > MAX_PROCFILE_BYTES:
        raise ValidationError(
            f"Procfile is larger than {MAX_PROCFILE_BYTES // 1024} KiB",
            details="A Procfile names a few processes.",
            field="Procfile",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Procfile could not be read: {exc}",
            details="Commit Procfile as UTF-8 text.",
            field="Procfile",
        ) from exc
    return parse_procfile(text)


def resolve_processes(root: Path) -> ProcessSet:
    """
    Name the process types a tree declares.

    The ``processes:`` block of ``noust.yaml`` when it has one, whole; otherwise the
    ``Procfile``; otherwise none. A ``noust.yaml`` is Noust's own file, so a block that is not
    valid is an error; a ``Procfile`` that is not valid is reported (:attr:`ProcessSet.problem`).

    Args:
        root: The tree being deployed, or the running code of an application.

    Returns:
        The process types.

    Raises:
        ValidationError: The file that applies is not valid; ``field`` names it.
    """
    # Imported here: project_file is the one reader of noust.yaml, and builds on this module.
    from noust.deployers.helpers.project_file import load_project_file

    block = load_project_file(root).declared.get("processes")
    if block is not None:
        return parse_processes_block(block)
    return read_procfile(root)


__all__ = [
    "PROCFILE",
    "RELEASE",
    "WEB",
    "ProcessSet",
    "ProcessType",
    "parse_processes_block",
    "parse_procfile",
    "read_procfile",
    "resolve_processes",
]
