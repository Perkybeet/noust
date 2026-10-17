# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How the commands that set a backup policy read what is kept (checklist H-04).

``--keep-gb`` is the same option on every policy that keeps backups (a
database's, an application's, its data's, Noust's own state), and so is the
fourth part of ``--to NAME[:COUNT[:DAYS[:GB]]]``: it is read once, here (rule 3).
"""

from __future__ import annotations

from typing import Any

import click


def gigabytes(value: str, option: str) -> float | None:
    """
    Read a retention by size: a number of gigabytes, or ``none`` for no limit.

    Args:
        value: What the operator typed.
        option: The option's name, for the error.

    Returns:
        The number, or None for ``none``.

    Raises:
        click.BadParameter: When it is neither.
    """
    if value.strip().lower() in ("none", "off", "no", ""):
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise click.BadParameter(f"{option} takes a number of gigabytes or 'none'") from exc


def add_size(entry: dict[str, Any], size: str) -> dict[str, Any]:
    """
    Give a destination entry the retention by size its ``--to`` named.

    Args:
        entry: ``{"name", "retention_count", "retention_days"}``.
        size: The fourth part of ``--to``, empty when there was none.

    Returns:
        The entry with ``retention_gb``: the size, or None.
    """
    entry["retention_gb"] = gigabytes(size, "--to") if size else None
    return entry


#: The help every command that takes ``--keep-gb`` shows.
KEEP_GB_HELP = (
    "The most the scheduled backups kept here may weigh together, in gigabytes, or 'none'. "
    "The newest always stays, whatever its size."
)
