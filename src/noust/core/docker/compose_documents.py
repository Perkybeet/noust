# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose document read by Compose on its standard input, and nowhere else.

Judging a change to a stack's compose file needs Compose's own answer to two questions about a document that
is not saved anywhere yet: what does it resolve to (``config``), and what would starting the stack from it do
(``up --dry-run``). Both are asked with the text on the standard input (``-f -``), so the document is written
to no file and no argument, and both change nothing: they are declared read-only in
:mod:`noust.core.docker.probes`. They are here, with the rest of what writes an argument list for Docker
(rule 1, and the ratchet of ``tests/test_docker_ratchet.py``); what is done with the answer is
:mod:`noust.deployers.compose_stack_edit`.

The project is the name Docker listed for the stack, never what was typed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from noust.core.runner import CommandRunner

__all__ = ["REHEARSE_TIMEOUT", "RESOLVE_TIMEOUT", "rehearse_document", "resolve_document"]

#: Seconds Compose gets to resolve a document, and to rehearse starting the stack from it.
RESOLVE_TIMEOUT = 60
REHEARSE_TIMEOUT = 180

_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}")


def _checked(project: str) -> str:
    """
    Args:
        project: A Compose project's name, as Docker listed it.

    Returns:
        It, when it is a name an argument may carry.

    Raises:
        ValueError: It is not (an option, a path, a name with a space).
    """
    if not _PROJECT.fullmatch(project):
        raise ValueError(f"{project!r} is not a Compose project's name")
    return project


def resolve_document(
    runner: CommandRunner, project: str, text: str, cwd: Path
) -> tuple[dict[str, Any] | None, str]:
    """
    Ask Compose for a document as it resolves it (``docker compose config``).

    The model holds the stack's secrets: it is parsed and dropped, never logged, and nothing of it is in
    what this returns when Compose fails; what is returned then is Compose's own words.

    Args:
        runner: Runs it.
        project: The stack's Compose project, as Docker listed it.
        text: The document, which goes on the standard input.
        cwd: Where Compose runs, which is where a relative path of the document means what it says.

    Returns:
        The model and an empty string; or None and what Compose said.
    """
    result = runner.run(
        ["docker", "compose", "-p", _checked(project), "-f", "-", "config", "--format", "json"],
        input=text,
        cwd=cwd,
        timeout=RESOLVE_TIMEOUT,
    )
    if not result.success:
        return None, (result.stderr or result.stdout).strip() or f"exit code {result.exit_code}"
    try:
        model = json.loads(result.stdout)
    except ValueError:
        return None, "Docker Compose printed something that is not a configuration"
    if not isinstance(model, dict):
        return None, "Docker Compose printed something that is not a configuration"
    return model, ""


def rehearse_document(
    runner: CommandRunner, project: str, text: str, cwd: Path
) -> tuple[bool, str]:
    """
    Ask Compose what starting a stack from a document would do, and change nothing
    (``docker compose up --dry-run --no-build``).

    Args:
        runner: Runs it.
        project: The stack's Compose project, as Docker listed it.
        text: The document, which goes on the standard input.
        cwd: Where Compose runs.

    Returns:
        Whether Compose could rehearse it, and what it said, verbatim.
    """
    result = runner.run(
        [
            "docker",
            "compose",
            "-p",
            _checked(project),
            "-f",
            "-",
            "up",
            "-d",
            "--dry-run",
            "--no-build",
        ],
        input=text,
        cwd=cwd,
        timeout=REHEARSE_TIMEOUT,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return result.success, output
