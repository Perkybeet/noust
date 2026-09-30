# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether the audit trail is working, for ``noust health`` and the console.

An audit failure used to be one line in the process log (``Cannot write audit
entry``) that nobody reads. Here it is state: every failed write, every
destination that stopped receiving, every configuration value that had to be
replaced is kept, and :func:`noust.core.audit.health` turns it into a verdict
with sentences an operator can act on.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SinkStatus:
    """
    How one destination is doing.

    Attributes:
        sink_id: The destination's name.
        ok: Whether the last attempt worked.
        error: The last error, verbatim.
        error_at: When it happened.
        lag_seconds: Age of the oldest event not delivered yet, when known.
    """

    sink_id: str
    ok: bool = True
    error: str | None = None
    error_at: str | None = None
    lag_seconds: float | None = None


@dataclass
class _State:
    failures: int = 0
    failing: bool = False
    last_failure: str | None = None
    last_failure_at: str | None = None
    recovered_at: str | None = None
    sinks: dict[str, SinkStatus] = field(default_factory=dict)


_lock = threading.Lock()
_state = _State()


def mark_failure(message: str) -> bool:
    """
    Record that an audit write failed.

    Args:
        message: What failed, verbatim.

    Returns:
        True when this is the first failure after working writes.
    """
    with _lock:
        first = not _state.failing
        _state.failing = True
        _state.failures += 1
        _state.last_failure = message
        _state.last_failure_at = _now()
        return first


def mark_success() -> bool:
    """
    Record that an audit write worked.

    Returns:
        True when writes had been failing until now: the caller records
        ``audit.recovered``.
    """
    with _lock:
        if not _state.failing:
            return False
        _state.failing = False
        _state.recovered_at = _now()
        return True


def mark_sink(sink_id: str, error: str | None, lag_seconds: float | None = None) -> bool:
    """
    Record how a delivery to a destination went.

    Args:
        sink_id: The destination.
        error: The error, or None when it worked.
        lag_seconds: Age of the oldest undelivered event, when known.

    Returns:
        True when the destination changed between working and failing.
    """
    with _lock:
        status = _state.sinks.setdefault(sink_id, SinkStatus(sink_id))
        changed = status.ok != (error is None)
        status.ok = error is None
        if error is not None:
            status.error = error
            status.error_at = _now()
        status.lag_seconds = lag_seconds
        return changed


def failure() -> tuple[bool, int, str | None, str | None]:
    """
    The write failure state.

    Returns:
        Whether writes are failing now, how many failed since the process
        started, the last error and when it happened.
    """
    with _lock:
        return _state.failing, _state.failures, _state.last_failure, _state.last_failure_at


def sinks() -> list[SinkStatus]:
    """
    The delivery state of every destination this process shipped to.

    Returns:
        Copies, so the caller cannot change the state.
    """
    with _lock:
        return [SinkStatus(**vars(status)) for status in _state.sinks.values()]


def reset() -> None:
    """Forget everything; for tests and for a fresh process state."""
    global _state
    with _lock:
        _state = _State()
