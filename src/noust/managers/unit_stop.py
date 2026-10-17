# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long an application's unit has to stop, and with which signal (checklist B-21).

systemd stops a unit by sending ``SIGTERM`` and waiting ``TimeoutStopSec`` (90
seconds unless the unit says otherwise) before it kills what is left. An
application that drains its connections, finishes a job or flushes a queue needs
longer and one that dies on ``SIGTERM`` may want ``SIGINT``; Coolify calls it the
stop grace period. It is the application's own number, kept with its other delivery
settings (:mod:`noust.core.app_rollout_store`), and written into its unit here.

The directives are one list built here, which the unit template renders when a unit
is created and :func:`with_stop_policy` writes into one that exists, so the two
cannot disagree (as for the limits and the hardening). Nothing restarts: systemd
reads the new numbers the next time it stops the unit.

The web process of the application is what the policy is for. The units of its side
processes and replicas, and a Compose stack's containers (which are Docker's:
``stop_grace_period`` in the compose file), are not touched.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from noust.core.app_rollout_store import Rollout
from noust.core.app_rollout_store import get as get_rollout
from noust.core.exceptions import ServiceError
from noust.core.store import App

#: The directives this module owns; :func:`with_stop_policy` removes them before it writes.
STOP_KEYS = frozenset({"TimeoutStopSec", "KillSignal"})


def directives(timeout: int | None, signal: str | None) -> list[str]:
    """
    Write a stop policy as unit directives.

    Args:
        timeout: Seconds, or None for systemd's.
        signal: A signal name, or None for ``SIGTERM``.

    Returns:
        One line per setting that is set, in a fixed order.
    """
    lines = []
    if timeout is not None:
        lines.append(f"TimeoutStopSec={timeout}")
    if signal is not None:
        lines.append(f"KillSignal={signal}")
    return lines


def policy_of(rollout: Rollout) -> list[str]:
    """
    Args:
        rollout: An application's delivery settings.

    Returns:
        Its stop policy as unit directives.
    """
    return directives(rollout.stop_timeout, rollout.stop_signal)


def with_stop_policy(unit: str, timeout: int | None, signal: str | None) -> str:
    """
    Put a stop policy into an existing unit, leaving everything else as it is.

    The unit is edited rather than rendered again, because an operator may have
    edited it by hand and a stop policy is no reason to throw that away. Any
    directive this module owns is removed from ``[Service]`` first; the new ones go
    after ``RestartSec=`` when the unit has it, which is where the template puts
    them, or at the end of the section.

    Args:
        unit: The unit file.
        timeout: Seconds, or None to remove ``TimeoutStopSec``.
        signal: A signal name, or None to remove ``KillSignal``.

    Returns:
        The unit with exactly this policy.

    Raises:
        ServiceError: The unit has no ``[Service]`` section.
    """
    section = ""
    start: int | None = None
    end: int | None = None
    anchor: int | None = None
    kept: list[str] = []
    for line in unit.split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section == "[Service]" and end is None:
                end = len(kept)
            section = stripped
            if section == "[Service]":
                start = len(kept)
        elif section == "[Service]":
            key = stripped.split("=", 1)[0].strip()
            if "=" in stripped and not stripped.startswith("#") and key in STOP_KEYS:
                continue
            if stripped.startswith("RestartSec="):
                anchor = len(kept) + 1
        kept.append(line)
    if start is None:
        raise ServiceError(
            "The unit has no [Service] section to put a stop policy in",
            details="Check the unit with 'systemctl cat <name>'; Noust writes one in every unit.",
        )
    if end is None:
        end = len(kept)
    while end > start + 1 and not kept[end - 1].strip():
        end -= 1
    position = anchor if anchor is not None else end
    return "\n".join(kept[:position] + directives(timeout, signal) + kept[position:])


def stop_policy_for_unit(unit: str, apps: Sequence[App]) -> list[str]:
    """
    Name the stop directives the unit of an application must be written with.

    The chokepoint ``ServiceManager`` asks whenever it writes an application's unit
    or its blue/green template, so whoever writes it - a deploy, a migration, the
    console - gives it the policy the application has.

    Args:
        unit: The unit's name without ``.service`` or the ``@``.
        apps: The applications the store records.

    Returns:
        The directives; empty when no application owns the unit or it has none.
    """
    for app in apps:
        if app.app_path and Path(app.app_path).name == unit:
            return policy_of(get_rollout(app.domain))
    return []


__all__ = [
    "STOP_KEYS",
    "directives",
    "policy_of",
    "stop_policy_for_unit",
    "with_stop_policy",
]
