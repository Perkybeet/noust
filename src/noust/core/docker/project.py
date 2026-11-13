# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The command that addresses a Compose project Noust wrote the file of.

An application's stack is addressed through its deployer, which reads the file, the project and the
profiles the operator chose in one place. What Noust runs for itself in a project of its own (a database
instance in a container, whose Compose file and project name Noust decides and nobody else edits) is
addressed by the project and the file alone, and this is the one place the argument list for that is
written (rule 1: an argument list, never a shell; and the door to Docker is this package).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["compose_argv"]


def compose_argv(project: str, compose_file: Path, *args: str) -> list[str]:
    """
    Build a ``docker compose`` command against a project and the file that makes it.

    Args:
        project: The Compose project, passed as ``-p`` so it never depends on the directory's name.
        compose_file: The Compose file.
        *args: The subcommand and its arguments (``up -d``, ``down``).

    Returns:
        The argument list.
    """
    return ["docker", "compose", "-p", project, "-f", str(compose_file), *args]
