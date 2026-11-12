# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Stopping what writes to Noust's store while its file is replaced or moved, and starting it again.

A restore of Noust's own state (:mod:`noust.managers.self_restore`) and the move of the store to
the state directory (:mod:`noust.managers.store_location`) both replace the file the console, the
monitor and the timers' jobs write to. They stop the same services and start the same ones again,
so the list and the two verbs are here (rule 3).
"""

from __future__ import annotations

from noust.core import paths
from noust.core.logger import Logger
from noust.core.runner import CommandResult, CommandRunner

__all__ = ["TIMER_PATTERNS", "WRITERS", "start_writers", "stop_writers"]

#: Deadline for a systemctl verb.
_SYSTEMCTL_TIMEOUT = 120

#: The units that write to the store, stopped while its file is replaced.
WRITERS = (paths.WEB_UNIT, paths.MONITOR_UNIT, paths.PREVIEWS_UNIT)

#: Timers that start jobs that write, stopped the same way.
TIMER_PATTERNS = (f"{paths.BACKUP_UNIT_PREFIX}*.timer", f"{paths.CRON_UNIT_PREFIX}*.timer")


def _systemctl(runner: CommandRunner, *args: str) -> CommandResult:
    """
    Run a systemctl verb.

    Args:
        runner: The runner every process goes through.
        args: Arguments after the program name.

    Returns:
        The command's outcome.
    """
    return runner.run(["systemctl", *args], timeout=_SYSTEMCTL_TIMEOUT)


def stop_writers(runner: CommandRunner, logger: Logger) -> list[str]:
    """
    Stop what writes to the store, remembering what was running.

    Args:
        runner: The runner every process goes through.
        logger: Says what is stopped.

    Returns:
        The services that were running and are stopped.
    """
    running: list[str] = []
    for unit in WRITERS:
        if _systemctl(runner, "is-active", "--quiet", f"{unit}.service").success:
            logger.info(f"Stopping {unit}")
            _systemctl(runner, "stop", f"{unit}.service")
            running.append(unit)
    # Timers whose jobs write; whatever matches nothing is fine.
    _systemctl(runner, "stop", *TIMER_PATTERNS)
    return running


def start_writers(runner: CommandRunner, logger: Logger, units: list[str]) -> None:
    """
    Start services again.

    Args:
        runner: The runner every process goes through.
        logger: Says what could not be started.
        units: Their names.
    """
    for unit in units:
        result = _systemctl(runner, "start", f"{unit}.service")
        if not result.success:
            logger.warning(
                f"Could not start {unit}: {result.stderr.strip() or 'see systemctl status'}"
            )
