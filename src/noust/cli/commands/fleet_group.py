# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust fleet`` group, on its own.

The verbs of the group live in several modules and each attaches itself to it. They import the
group from here and not from ``noust.cli.commands.fleet``, the entry point, so that module can
import them all without any of them importing it back.
"""

from __future__ import annotations

import click

from noust.cli.app import NoustGroup


@click.group("fleet", cls=NoustGroup)
def cli() -> None:
    """Enroll this server in a fleet, or see the fleet from its central."""
