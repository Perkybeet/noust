# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake runner that also says what environment the real runner would have started each command with.

``FakeRunner`` records the ``env=`` a caller added, which is what a test of a manager needs. A test of what
Noust keeps out of a command (``tests/test_compose_environment.py``) needs the whole environment of it:
what the process holds, minus what is Noust's for a command that reads a file of a stack, plus what the caller
passed on purpose. This is composed by the same function the real runner uses.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from noust.core.command_environment import command_environment
from noust.core.runner import CommandResult, FakeRunner, clean_environment


class EnvironmentRunner(FakeRunner):
    """A :class:`FakeRunner` that remembers the environment each command would have started with."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        #: ``(argv, environment)`` of every run and stream, in order.
        self.started_with: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def _note(self, argv: Sequence[str], env: Any, clean_env: bool) -> None:
        base = clean_environment(os.environ) if clean_env else os.environ
        self.started_with.append((tuple(argv), command_environment(argv, base, env)))

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        self._note(argv, kwargs.get("env"), bool(kwargs.get("clean_env")))
        return super().run(argv, **kwargs)

    def stream(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        self._note(argv, kwargs.get("env"), bool(kwargs.get("clean_env")))
        return super().stream(argv, **kwargs)

    def environment_of(self, prefix: Sequence[str], *, containing: str = "") -> dict[str, str]:
        """
        Args:
            prefix: The start of the command's argv.
            containing: A word its argv must also have.

        Returns:
            The environment the last matching command would have been started with.

        Raises:
            LookupError: No command matches.
        """
        size = len(prefix)
        for argv, found in reversed(self.started_with):
            if argv[:size] == tuple(prefix) and (not containing or containing in argv):
                return found
        raise LookupError(f"no call of {tuple(prefix)} with {containing!r} was made")
