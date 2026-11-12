# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The commands of the gate to Docker that only look.

A ``--dry-run`` executes a command only when its exact shape is declared read-only (the language is
described in :mod:`noust.core.runner`), because a rehearsal that cannot look at the machine reports
fiction. These are the shapes the gate asks with; the actions (start, stop, restart, remove) are not
here, so a rehearsal never runs them.
"""

from __future__ import annotations

#: Read by :mod:`noust.core.runner` (listed in its ``PROBE_MODULES``).
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    ("docker", "version", "--format", "*"),
    ("docker", "info", "--format", "*"),
    ("docker", "compose", "version"),
    ("docker", "context", "inspect"),
    ("docker", "ps", "--all", "--no-trunc", "--format", "*"),
    ("docker", "image", "ls", "--no-trunc", "--format", "*"),
    ("docker", "volume", "ls", "--format", "*"),
    ("docker", "network", "ls", "--no-trunc", "--format", "*"),
    ("docker", "system", "df", "-v", "--format", "*"),
    ("docker", "system", "df", "--format", "*"),
    ("docker", "inspect", "--type", "*", "*"),
)
