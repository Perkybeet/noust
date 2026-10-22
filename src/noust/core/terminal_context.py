# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a job function reports its progress through, when it runs in a terminal.

The deploy and update functions of the console are job functions: they take a
context and tell it what they are doing. The CLI has no console to queue them
in, so a command that wants the same function (a preview made by hand, a clone)
runs it in its own process with this context, which prints the progress.
"""

from __future__ import annotations

from typing import Any

from noust.core.logger import Logger


class TerminalContext:
    """
    What a build run from the terminal reports its progress through.

    The CLI has no console to queue jobs in, so a preview it builds runs in its
    own process; the job functions only need something to tell progress to.
    """

    job_id: str | None = None
    is_cancelled = False

    def __init__(self, logger: Logger) -> None:
        """
        Args:
            logger: Where progress is printed.
        """
        self._logger = logger

    def update(self, step: str, progress: int) -> None:
        """
        Args:
            step: What is happening.
            progress: How far along, in percent.
        """
        self._logger.substep(step)

    def log(self, message: str, level: str = "info") -> None:
        """
        Args:
            message: What to say.
            level: Its severity.
        """
        if level == "warning":
            self._logger.warning(message)
        elif level == "error":
            self._logger.error(message)
        else:
            self._logger.substep(message)

    def set_metadata(self, key: str, value: Any) -> None:
        """
        Args:
            key: Ignored.
            value: Ignored.
        """

    def add_secret_env(self, env: Any) -> None:
        """
        Args:
            env: Ignored: the deployment's own recorder scrubs its log.
        """

    def set_result(self, value: Any) -> None:
        """
        Args:
            value: Ignored.
        """
