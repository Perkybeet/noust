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
    # What state a container is in, for the connector of a tunnel (:mod:`noust.core.docker.connector`).
    ("docker", "inspect", "--type", "container", "--format", "*", "*"),
    # Asking a registry for the manifest of a tag and the config behind it downloads no layer and moves
    # no local tag (:mod:`noust.core.docker.registry`); a pull does both, and is not here.
    ("docker", "buildx", "imagetools", "inspect", "*"),
    ("docker", "buildx", "imagetools", "inspect", "--format", "*", "*"),
    # What a local image says of itself, for any number of images.
    ("docker", "inspect", "--type", "image", "--format", "*", ...),
    # A document read on the standard input (``-f -``): what Compose resolves it to, and what starting the
    # stack from it would do, rehearsed (``--dry-run``). Neither writes the document anywhere nor changes
    # a container; they are how an edit is judged before it is saved.
    ("docker", "compose", "-p", "*", "-f", "-", "config", "--format", "json"),
    ("docker", "compose", "-p", "*", "-f", "-", "up", "-d", "--dry-run", "--no-build"),
)
