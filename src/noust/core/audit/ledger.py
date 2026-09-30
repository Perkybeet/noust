# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The host action ledger: what Noust actually did to the machine, as root.

An API call says "restart shop"; the ledger says ``systemctl restart
shop-example-com.service`` ran, and which file under ``/etc/nginx`` changed.
Both are linked by the correlation id the console bound for the request, the
CLI for the command or the job manager for the job, which is the evidence
that separates "the API asked for A" from "the machine did B" (a confused
deputy), and the "on what" of ENS op.exp.8.1 for system actions.

It can be complete because there are exactly two ways Noust changes the
machine: :class:`~noust.core.runner.CommandRunner` for processes (rule 1) and
:mod:`noust.core.fs` for files. Both call the listeners installed here. Only
changes are recorded (``audit.host_activity: mutations``, the default): the
runner's read-only probes are left out unless it is ``all``. A deploy is a
hundred-odd actions, so one correlation id records at most
:data:`MAX_PER_CORRELATION` and then says it stopped (``host.truncated``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from noust.core import fs as fs_module
from noust.core.audit.context import current_correlation_id
from noust.core.audit.log import writing_now
from noust.core.fs import is_rehearsal

logger = logging.getLogger(__name__)

#: Host actions recorded for one correlation id.
MAX_PER_CORRELATION = 500

#: Correlation ids whose counts are remembered at once.
MAX_TRACKED = 1024

_lock = threading.Lock()
_counts: OrderedDict[str, int] = OrderedDict()
_installed = False


def _budget(correlation: str | None) -> str | None:
    """
    Count one host action against its correlation id.

    Args:
        correlation: The id, or None for actions outside any request,
            command or job (counted per hour instead).

    Returns:
        ``"record"`` to record it, ``"truncate"`` to record that recording
        stops, or None to skip it.
    """
    key = correlation or f"none:{int(time.time() // 3600)}"
    with _lock:
        count = _counts.pop(key, 0) + 1
        _counts[key] = count
        while len(_counts) > MAX_TRACKED:
            _counts.popitem(last=False)
    if count <= MAX_PER_CORRELATION:
        return "record"
    if count == MAX_PER_CORRELATION + 1:
        return "truncate"
    return None


def _skip(path: Path | None = None) -> bool:
    from noust.core.audit import get_log

    if writing_now() or is_rehearsal():
        return True
    log = get_log()
    if not log.enabled or log.settings.host_activity == "off":
        return True
    if path is not None:
        # The trail's own files (log, key, cursors) change because of an
        # event; recording those changes would feed on itself.
        try:
            Path(path).resolve().relative_to(log.path.parent.resolve())
        except ValueError:
            return False
        return True
    return False


def _record(event: str, target: str, details: dict[str, Any]) -> None:
    from noust.core.audit import get_log

    correlation = current_correlation_id()
    decision = _budget(correlation)
    if decision is None:
        return
    if decision == "truncate":
        get_log().append(
            "host.truncated",
            target=target,
            details={"limit": MAX_PER_CORRELATION},
        )
        return
    get_log().append(event, target=target, details=details)


def on_file_change(operation: str, path: Path, destination: Path | None = None) -> None:
    """
    Record one change the filesystem seam made.

    Args:
        operation: ``write``, ``mkdir``, ``remove``, ``remove_tree``,
            ``move``, ``rename``, ``copy_tree``, ``chmod`` or ``symlink``.
        path: What was changed.
        destination: Where it went, for a move, a rename, a copy or a link.
    """
    if _skip(path):
        return
    details: dict[str, Any] = {"op": operation}
    if destination is not None:
        details["to"] = str(destination)
    _record("host.fs", str(path), details)


def on_execution(
    argv: Sequence[str] | Any,
    *,
    exit_code: int | None = None,
    duration: float | None = None,
    user: str | None = None,
    cwd: Path | str | None = None,
    read_only: bool | None = None,
    **_extra: Any,
) -> None:
    """
    Record one process the runner executed.

    The argv is the runner's redacted one: secrets passed as arguments are
    already masked, and nothing of the environment is recorded.

    Args:
        argv: The command, redacted; or an object carrying ``argv`` and any of
            the other fields as attributes, whichever shape the runner
            reports an execution in.
        exit_code: How it ended, when known.
        duration: Seconds it took, when known.
        user: The account it ran as, when not root.
        cwd: Its working directory.
        read_only: Whether it only observes; classified with
            :func:`~noust.core.runner.is_read_only` when None.
        **_extra: Anything else the runner reports, ignored.
    """
    from noust.core.audit import get_log
    from noust.core.runner import is_read_only

    if hasattr(argv, "argv"):
        report = argv
        argv = report.argv
        exit_code = getattr(report, "exit_code", exit_code)
        duration = getattr(report, "duration", duration)
        user = getattr(report, "user", user)
        cwd = getattr(report, "cwd", cwd)
        read_only = getattr(report, "read_only", read_only)
    if _skip() or not argv:
        return
    probe = is_read_only(argv) if read_only is None else read_only
    if probe and get_log().settings.host_activity != "all":
        return
    details: dict[str, Any] = {"argv": [str(arg) for arg in argv]}
    if exit_code is not None:
        details["exit_code"] = exit_code
    if duration is not None:
        details["duration_ms"] = int(duration * 1000)
    if user:
        details["user"] = user
    if cwd:
        details["cwd"] = str(cwd)
    if probe:
        details["read_only"] = True
    _record("host.exec", Path(str(argv[0])).name, details)


def install_ledger() -> bool:
    """
    Start recording host actions in this process. Idempotent.

    Returns:
        True when the runner reports executions too; False when only file
        changes are recorded because the runner has no listener hook.
    """
    global _installed
    import noust.core.runner as runner_module

    # A module-level function or one on the runner class: a registry shared
    # by every runner, since --dry-run and the tests swap the runner itself.
    add_execution = getattr(runner_module, "add_execution_listener", None) or getattr(
        runner_module.CommandRunner, "add_execution_listener", None
    )
    with _lock:
        if not _installed:
            fs_module.add_change_listener(on_file_change)
            # INTEGRATION POINT (workstream B4): the runner's execution hook.
            # When CommandRunner gains add_execution_listener(callable), every
            # process it runs is reported to on_execution; until then only
            # file changes reach the ledger.
            if add_execution is not None:
                add_execution(on_execution)
            _installed = True
    return add_execution is not None


def uninstall_ledger() -> None:
    """Stop recording host actions; for tests and for a process shutting down."""
    global _installed
    import noust.core.runner as runner_module

    with _lock:
        if _installed:
            fs_module.remove_change_listener(on_file_change)
            remove_execution = getattr(runner_module, "remove_execution_listener", None) or getattr(
                runner_module.CommandRunner, "remove_execution_listener", None
            )
            if remove_execution is not None:
                remove_execution(on_execution)
            _installed = False
        _counts.clear()
