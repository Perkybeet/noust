# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What systemd will run for a unit, asked of systemd and never read from the file.

A unit's file says what the author wrote; what systemd runs is that file with every addition of the
unit's ``.d`` directory applied on top, and an addition can take away what the file gave (an empty
``ExecStartPre=`` forgets every command of that directive before it). Whoever needs to know whether
something Noust put in a unit will run asks this, once, through the one runner that every command of
Noust goes through, with a deadline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from noust.managers.service_manager import ServiceManager
from noust.validators.names import validate_service_name

#: What is asked of systemd. ``ExecStart`` and ``DropInPaths`` are always there for a loaded service, so
#: an answer without them is one systemd did not give about this unit.
PROPERTIES = ("ExecStartPre", "ExecReload", "ExecStart", "DropInPaths")

#: Deadline of ``systemctl show``, which only reads what the manager already holds.
SHOW_TIMEOUT = 30

_ARGV = re.compile(r"argv\[\]=(?P<argv>.*?) ; ignore_errors=")


@dataclass(frozen=True)
class UnitExecution:
    """
    What systemd runs for a unit.

    Attributes:
        pre: The command of each ``ExecStartPre``, as systemd prints its argv, in the order it runs them.
        reload: The same for ``ExecReload``.
        start: The same for ``ExecStart``.
        drop_ins: The additions systemd read, in the order it applied them.
        problem: Why systemd could not say (the fields above are then empty); None when it did.
    """

    pre: tuple[str, ...] = ()
    reload: tuple[str, ...] = ()
    start: tuple[str, ...] = ()
    drop_ins: tuple[str, ...] = ()
    problem: str | None = None


def _argv_of(value: str) -> str:
    """Take the command out of the ``{ path=... ; argv[]=... ; ... }`` systemd prints for one."""
    found = _ARGV.search(value)
    return found.group("argv").strip() if found else value.strip()


def read_execution(services: ServiceManager, name: str) -> UnitExecution:
    """
    Ask systemd what it runs for a unit.

    Args:
        services: The unit manager, whose runner asks (the dry run's, when the command is one: this
            is a declared read-only probe, so a rehearsal asks it for real).
        name: The unit's name, with or without ``.service``.

    Returns:
        What it runs, or the reason it could not say. Nothing is ever guessed: a failed or empty
        answer is a ``problem``, never an empty list of commands.

    Raises:
        ValidationError: ``name`` is not a safe unit name.
    """
    unit = f"{validate_service_name(str(name).removesuffix('.service'))}.service"
    argv = ["systemctl", "show", unit]
    for prop in PROPERTIES:
        argv += ["-p", prop]
    if not services.runner.exists("systemctl"):
        return UnitExecution(problem="systemctl is not available on this machine")
    result = services.runner.run(argv, timeout=SHOW_TIMEOUT)
    if result.timed_out:
        return UnitExecution(problem=f"systemctl show did not answer in {SHOW_TIMEOUT} seconds")
    if not result.success:
        said = (result.stderr or result.stdout).strip().splitlines()
        return UnitExecution(
            problem=said[0] if said else f"systemctl show ended with status {result.exit_code}"
        )
    found: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in PROPERTIES:
            found.setdefault(key, []).append(value)
    if "ExecStart" not in found and "DropInPaths" not in found:
        return UnitExecution(problem=f"systemd did not say what {unit} runs")
    return UnitExecution(
        pre=tuple(_argv_of(value) for value in found.get("ExecStartPre", [])),
        reload=tuple(_argv_of(value) for value in found.get("ExecReload", [])),
        start=tuple(_argv_of(value) for value in found.get("ExecStart", [])),
        drop_ins=tuple(path for value in found.get("DropInPaths", []) for path in value.split()),
    )
