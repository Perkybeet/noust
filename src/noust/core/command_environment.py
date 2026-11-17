# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What environment a command is started with, when it reads a file somebody else wrote.

``docker compose`` fills ``${NAME}`` in a Compose document, and reads ``secrets: {x: {environment:
NAME}}``, from the environment of the process that runs it, and the runner hands a command the
whole environment of Noust's own process. A repository's file could therefore name a variable Noust
reads for itself (a token, the sealing key, the data directory) and have its value put into a
container or an image. Every command that reads such a file, ``docker compose`` and the ways of
building an image, is started without ``NOUST_*``, decided in one place that the runner goes
through, so no caller can forget it. What the operator has in the environment (``DOCKER_HOST``,
``COMPOSE_*``, ``PATH``, a variable of their own) and what the caller passes on purpose stay.

Kept beside :mod:`noust.core.runner`, which does not grow.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

#: What Noust reads for itself starts with this (``NOUST_TOKEN``, ``NOUST_DATA_DIR``...). The names
#: of before 4.0 were never environment variables, so there is no other prefix.
NOUST_ENV_PREFIX = "NOUST_"

#: The ``docker`` subcommands whose input is a file somebody else wrote and which fill ``${NAME}``
#: from their environment: Compose, and the ways of building an image.
_FILE_READING_SUBCOMMANDS = frozenset({"compose", "build", "buildx", "builder"})

#: The options of ``docker`` itself that take a value, so what follows one is not the subcommand.
_DOCKER_VALUE_OPTIONS = frozenset(
    {
        "-H",
        "--host",
        "-c",
        "--context",
        "--config",
        "-l",
        "--log-level",
        "--tlscacert",
        "--tlscert",
        "--tlskey",
    }
)


def reads_a_file_of_a_stack(argv: Sequence[str]) -> bool:
    """
    Tell whether a command reads a file somebody else wrote and fills it in from its environment.

    Args:
        argv: The command.

    Returns:
        True for ``docker compose ...``, ``docker-compose``, and ``docker build``, ``buildx``,
        ``builder`` and ``image build``, whatever options of ``docker`` come before the subcommand.
    """
    if not argv:
        return False
    program = os.path.basename(argv[0])
    if program == "docker-compose":
        return True
    if program != "docker":
        return False
    words = iter(argv[1:])
    for word in words:
        if word in _DOCKER_VALUE_OPTIONS:
            next(words, None)
            continue
        if word.startswith("-"):
            continue
        if word in _FILE_READING_SUBCOMMANDS:
            return True
        if word == "image":
            return next(words, "") == "build"
        return False
    return False


def command_environment(
    argv: Sequence[str], base: Mapping[str, str], extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """
    Compose the environment a command is started with.

    Args:
        argv: The command.
        base: What it would inherit: this process's environment, or the allowed part of it.
        extra: What the caller adds on purpose; it wins over everything, even a name like Noust's.

    Returns:
        The environment: ``base`` without any ``NOUST_*`` variable when the command reads a file of
        a stack (:func:`reads_a_file_of_a_stack`), plus ``extra``.
    """
    composed = dict(base)
    if reads_a_file_of_a_stack(argv):
        composed = {
            name: value for name, value in composed.items() if not name.startswith(NOUST_ENV_PREFIX)
        }
    if extra:
        composed.update(extra)
    return composed
