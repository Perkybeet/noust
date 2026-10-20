# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The priority a backup runs at.

At 02:00 a dump piped through gzip competes for CPU and disk with the applications of a small
server. Every unit a backup runs in is written with a priority, from three settings an operator
can change:

* ``backup.nice``: the nice value, 0 to 19 (10 by default);
* ``backup.io_class``: ``best-effort`` (the default, at ``backup.io_priority`` 0 to 7, 7 by default:
  the last of its class) or ``idle`` (only when nothing else wants the disk);
* ``backup.cpu_quota``: a ceiling in percent of one CPU (``50`` is half a core, ``200`` two), or
  ``none`` (the default).

The block is written between two marker comments right after ``[Service]``, in one place
(:meth:`~noust.managers.backup_scheduler.BackupScheduler._write_unit`), and replaced rather than
added to, so a unit written by an earlier version is brought along by running
:meth:`~noust.managers.backup_scheduler.BackupScheduler.apply_priority` and a second pass changes
nothing. The values are validated here before they are written into a root-owned file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from noust.core.exceptions import ValidationError

__all__ = [
    "DEFAULT_NICE",
    "BackupPriority",
    "current_priority",
    "priority_from_config",
    "with_priority",
]

#: How nice a backup is when the operator says nothing.
DEFAULT_NICE = 10

#: The I/O classes systemd offers that a backup may ask for (``realtime`` could starve the server).
IO_CLASSES = ("best-effort", "idle")

#: The markers that fence the block, so it can be found and replaced.
_BEGIN = "# noust-priority"
_END = "# /noust-priority"


class _Settings(Protocol):
    """What :func:`priority_from_config` reads: the configuration."""

    def get(self, key: str, default: Any = None) -> Any:
        """Read one setting."""


@dataclass(frozen=True)
class BackupPriority:
    """
    How a backup is scheduled against everything else on the server.

    Attributes:
        nice: The nice value, 0 (as an application) to 19 (the last).
        io_class: ``best-effort`` or ``idle``.
        io_priority: 0 (first) to 7 (last) inside ``best-effort``; ignored for ``idle``.
        cpu_quota: A ceiling in percent of one CPU; None for none.
    """

    nice: int = DEFAULT_NICE
    io_class: str = "best-effort"
    io_priority: int = 7
    cpu_quota: int | None = None

    def lines(self) -> list[str]:
        """
        Returns:
            The ``[Service]`` directives this priority is, fenced by the markers.
        """
        found = [_BEGIN, f"Nice={self.nice}", f"IOSchedulingClass={self.io_class}"]
        if self.io_class == "best-effort":
            found.append(f"IOSchedulingPriority={self.io_priority}")
        if self.cpu_quota is not None:
            found.append(f"CPUQuota={self.cpu_quota}%")
        found.append(_END)
        return found


def _whole(value: Any, key: str, low: int, high: int) -> int:
    """
    Read a whole number in a range.

    Args:
        value: What the configuration holds.
        key: The setting, for the error.
        low: The least allowed.
        high: The most allowed.

    Returns:
        The number.

    Raises:
        ValidationError: It is not a whole number from ``low`` to ``high``.
    """
    try:
        number = int(str(value).strip())
    except ValueError:
        number = low - 1
    if isinstance(value, bool) or not low <= number <= high:
        raise ValidationError(
            f"{key} must be a whole number from {low} to {high}",
            details=f"It is {value!r}. Change it with: noust config set {key} VALUE",
            field=key,
        )
    return number


def priority_from_config(config: _Settings) -> BackupPriority:
    """
    Read the priority from the configuration.

    Args:
        config: The configuration.

    Returns:
        The priority; the defaults for what is not set.

    Raises:
        ValidationError: A setting is out of its range or not a known class.
    """
    nice = _whole(config.get("backup.nice", DEFAULT_NICE), "backup.nice", 0, 19)
    io_class = str(config.get("backup.io_class", "best-effort")).strip().lower()
    if io_class not in IO_CLASSES:
        raise ValidationError(
            f"backup.io_class must be one of: {', '.join(IO_CLASSES)}",
            details=f"It is {io_class!r}. Change it with: noust config set backup.io_class idle",
            field="backup.io_class",
        )
    io_priority = _whole(config.get("backup.io_priority", 7), "backup.io_priority", 0, 7)
    quota_setting = config.get("backup.cpu_quota")
    quota: int | None = None
    if quota_setting is not None and str(quota_setting).strip().lower() not in (
        "",
        "none",
        "off",
        "no",
    ):
        quota = _whole(quota_setting, "backup.cpu_quota", 1, 10000)
    return BackupPriority(nice, io_class, io_priority, quota)


def current_priority() -> BackupPriority:
    """
    Read the priority from this server's configuration.

    Returns:
        The priority.

    Raises:
        ValidationError: A setting is not valid.
    """
    from noust.core.config import Config

    return priority_from_config(Config())


def with_priority(text: str, priority: BackupPriority) -> str:
    """
    Put a priority into a service unit's text, replacing the one it had.

    Args:
        text: A unit file.
        priority: What to run it at.

    Returns:
        The text with the block right after ``[Service]``; the text as it was when it has no
        such section (a timer).
    """
    lines = text.split("\n")
    if "[Service]" not in lines:
        return text
    kept: list[str] = []
    inside = False
    for line in lines:
        if line == _BEGIN:
            inside = True
            continue
        if inside:
            if line == _END:
                inside = False
            continue
        kept.append(line)
    at = kept.index("[Service]") + 1
    return "\n".join([*kept[:at], *priority.lines(), *kept[at:]])
