# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What happens to a change when the audit trail cannot be written (I-40).

Two answers exist, and an operator picks one (``audit.on_failure``):

- ``warn`` (the default outside the ENS profile): the change is made and the
  failure is loud. An action is never lost because the log could not take its
  line; the failure is state (:mod:`noust.core.audit.status`), shown in the
  console and ``noust health``, and an ``audit.recovered`` event follows the
  first write that works again.
- ``refuse`` (always under ``security.profile: ens-medium``, which allows no
  unaudited change): while the trail cannot take an event, every change is
  refused at the two doors changes come through - the API's middleware and the
  command line's audit wrapper - with the reason and how to fix it. Reading,
  signing in and the commands that repair the trail stay open, or the server
  could not be mended from where it is broken.

"Cannot be written" is judged twice: by what already failed in this process
(:func:`noust.core.audit.status.failure`), and by a look at the log's
directory *before* the change, so the first change after the disk filled or
the directory went read-only is already refused rather than the last one made
blind. A look is a ``stat`` and an ``access``, never a write, so it cannot
itself lengthen the outage.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from noust.core.audit import status
from noust.core.ens import profile
from noust.core.fs import is_rehearsal

MODE_WARN = "warn"
MODE_REFUSE = "refuse"
MODES = (MODE_WARN, MODE_REFUSE)

#: The least free space on the log's disk that still counts as writable.
MIN_FREE_BYTES = 1024 * 1024

#: Command groups that stay open while changes are refused: they are how the
#: trail, the profile and the machine are put right.
REPAIR_COMMANDS = frozenset({"audit", "health", "ens", "config", "setup", "incident", "web"})


def mode(config: Any | None = None) -> str:
    """
    Which of the two answers applies.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        ``refuse`` under the ENS profile and wherever ``audit.on_failure`` says
        so; ``warn`` otherwise. An unreadable value is ``refuse`` under the
        profile and ``warn`` outside it: a typo must not stop a server nor
        loosen one.
    """
    if profile.defaults_for(profile.current_profile(config)).audit_fail_closed:
        return MODE_REFUSE
    if config is None:
        from noust.core.config import Config

        config = Config()
    return (
        MODE_REFUSE
        if str(config.get("audit.on_failure") or "").strip() == MODE_REFUSE
        else MODE_WARN
    )


def unavailable(log_path: Path | None = None) -> str | None:
    """
    Whether the trail can take an event now, and if not, why.

    Args:
        log_path: The log file; the process's by default.

    Returns:
        A sentence saying why it cannot, or None when it can.
    """
    failing, _count, message, _when = status.failure()
    if failing:
        return f"the last write failed: {message}"
    if log_path is None:
        from noust.core.audit import get_log

        log = get_log()
        if not log.enabled:
            return None
        log_path = log.path
    directory = log_path.parent
    try:
        if not directory.is_dir():
            return f"{directory} does not exist"
        if not os.access(directory, os.W_OK):
            return f"{directory} cannot be written to"
        if log_path.exists() and not os.access(log_path, os.W_OK):
            return f"{log_path} cannot be written to"
        free = shutil.disk_usage(directory).free
    except OSError as exc:
        return f"{directory} cannot be read: {exc.strerror or exc}"
    if free < MIN_FREE_BYTES:
        return f"the disk of {directory} is full ({free // 1024} KiB free)"
    return None


def refusal(config: Any | None = None) -> str | None:
    """
    Whether a change must be refused now, and the sentence that says why.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        The reason, or None when the change may go ahead (the policy is to warn,
        or the trail works, or this is a rehearsal that writes nothing).
    """
    if is_rehearsal() or mode(config) != MODE_REFUSE:
        return None
    return unavailable()


#: The words every refusal carries, at the API and at the command line.
HINT = (
    "Fix the audit log (noust audit status says what is wrong), or set audit.on_failure to warn if this server "
    "is not under the ENS profile. Reading, signing in and noust audit still work."
)
