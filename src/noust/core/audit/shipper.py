# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deliver the audit log to its destinations, from a saved cursor.

The local log is the queue: it is already on disk, in order, and nothing in
it is deleted before every destination has received it (see
:meth:`~noust.core.audit.log.AuditLog.purge`). Each destination has a cursor,
the ``seq`` of the last event it acknowledged, kept in ``audit-ship.json``
(0600) beside the log. A round sends what follows the cursor and moves it;
a destination that fails keeps its cursor and is retried with a growing
delay, so an outage costs a late delivery, never a lost event. Delivery is
therefore *at least once*: a receiver deduplicates with ``id`` or ``seq``.

The console runs rounds in its audit worker; ``noust audit ship`` runs one
from a timer on a server without the console.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.audit import status
from noust.core.audit.actor import Actor
from noust.core.audit.log import AuditLog
from noust.core.audit.sinks import Sink, shipping_sinks

logger = logging.getLogger(__name__)

STATE_FILE_NAME = "audit-ship.json"

#: Events sent to one destination in one round, so a long backlog does not
#: starve the others.
BATCH = 5000

#: Longest wait between attempts at a failing destination, in seconds.
MAX_BACKOFF = 300


def state_path_for(log: AuditLog) -> Path:
    """
    Where a log's shipping cursors are kept.

    Args:
        log: The log.

    Returns:
        ``audit-ship.json`` beside it.
    """
    return log.path.parent / STATE_FILE_NAME


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    """
    Read the shipping cursors.

    Args:
        path: The state file.

    Returns:
        Destination to its cursor record; empty when there is no file or it
        cannot be parsed (every destination then starts afresh).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable audit shipping state %s: %s", path, exc)
        return {}
    sinks = raw.get("sinks") if isinstance(raw, dict) else None
    return {str(key): value for key, value in (sinks or {}).items() if isinstance(value, dict)}


def _save_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    # Written beside the log with os primitives, not the filesystem seam:
    # the seam reports each change to the host action ledger, and a cursor
    # moving on every round would then record itself forever.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps({"sinks": state}, sort_keys=True, indent=2).encode("utf-8")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _age_seconds(timestamp: Any, now: float) -> float | None:
    try:
        moment = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, now - moment.timestamp())


class Shipper:
    """Send what each destination has not received yet."""

    def __init__(
        self,
        log: AuditLog,
        sinks: list[Sink] | None = None,
        *,
        state_path: Path | None = None,
        batch: int = BATCH,
        clock: Any = time.time,
    ) -> None:
        """
        Args:
            log: The log to ship.
            sinks: The destinations; built from the settings when None.
            state_path: Where cursors are kept; beside the log by default.
            batch: Events per destination per round.
            clock: Wall-clock source, for tests.
        """
        self.log = log
        self._sinks = sinks
        self.state_path = state_path or state_path_for(log)
        self.batch = batch
        self._clock = clock

    @property
    def sinks(self) -> list[Sink]:
        """The destinations, built from the settings on first use."""
        if self._sinks is None:
            self._sinks = shipping_sinks(self.log.settings)
        return self._sinks

    def close(self) -> None:
        """Drop every open connection."""
        for sink in self._sinks or []:
            sink.close()

    def lowest_cursor(self) -> int | None:
        """
        The oldest event some destination has not acknowledged yet.

        Returns:
            The smallest cursor among the configured destinations, or None
            when nothing ships (retention then waits for nobody).
        """
        if not self.sinks:
            return None
        state = load_state(self.state_path)
        cursors = [int(state.get(sink.sink_id, {}).get("seq", 0)) for sink in self.sinks]
        return min(cursors) if cursors else None

    def ship_once(self) -> dict[str, int]:
        """
        Run one round over every destination.

        Returns:
            Destination to how many events it received this round.

        Raises:
            OSError: The cursor file cannot be written.
        """
        if not self.sinks or not self.log.enabled:
            return {}
        state = load_state(self.state_path)
        head = self.log.head()
        delivered: dict[str, int] = {}
        followups: list[tuple[str, str, dict[str, Any]]] = []
        for sink in self.sinks:
            record = state.setdefault(sink.sink_id, {})
            if "seq" not in record:
                # A new destination starts from now unless it asked for the
                # history: a year of retained events is rarely what a newly
                # configured receiver expects first.
                backfill = bool(getattr(getattr(sink, "destination", None), "backfill", False))
                record["seq"] = 0 if backfill or head is None else head[0]
            delivered[sink.sink_id] = self._ship(sink, record, followups)
        _save_state(self.state_path, state)
        for event, sink_id, details in followups:
            self.log.append(
                event, actor=Actor.system("audit"), target=f"sink:{sink_id}", details=details
            )
        return delivered

    def _ship(
        self,
        sink: Sink,
        record: dict[str, Any],
        followups: list[tuple[str, str, dict[str, Any]]],
    ) -> int:
        now = self._clock()
        if now < float(record.get("next_attempt", 0)):
            return 0
        count = 0
        pending_ts: Any = None
        try:
            for entry in self.log.iter_since(int(record["seq"])):
                pending_ts = entry.get("ts")
                sink.send(entry)
                record["seq"] = entry["seq"]
                record["delivered_at"] = datetime.now(timezone.utc).isoformat()
                pending_ts = None
                count += 1
                if count >= self.batch:
                    break
        except OSError as exc:
            failures = int(record.get("failures", 0)) + 1
            record["failures"] = failures
            record["error"] = str(exc) or type(exc).__name__
            record["error_at"] = datetime.now(timezone.utc).isoformat()
            record["next_attempt"] = now + min(MAX_BACKOFF, 5 * 2 ** min(failures, 6))
            record["pending_since"] = record.get("pending_since") or pending_ts
            lag = _age_seconds(record["pending_since"], now)
            status.mark_sink(sink.sink_id, record["error"], lag)
            limit = self.log.settings.sink_lag_minutes * 60
            if lag is not None and lag > limit and not record.get("degraded"):
                record["degraded"] = True
                followups.append(
                    (
                        "audit.sink.degraded",
                        sink.sink_id,
                        {"error": record["error"], "lag_seconds": int(lag)},
                    )
                )
            if failures == 1:
                logger.warning("Cannot ship audit events to %s: %s", sink.sink_id, exc)
            return count
        if record.get("degraded"):
            followups.append(("audit.sink.recovered", sink.sink_id, {"delivered": count}))
        for key in ("failures", "error", "error_at", "next_attempt", "pending_since", "degraded"):
            record.pop(key, None)
        status.mark_sink(sink.sink_id, None, 0.0)
        return count


def sink_report(log: AuditLog) -> list[dict[str, Any]]:
    """
    The shipping state of every destination, as saved by the last round.

    Args:
        log: The log.

    Returns:
        One record per destination: its ``sink_id``, cursor ``seq``, the last
        ``error``, ``lag_seconds`` behind the log and whether it is
        ``degraded``. journald, written as events are recorded, has no cursor
        and appears only when this process saw it fail.
    """
    state = load_state(state_path_for(log))
    now = time.time()
    report: list[dict[str, Any]] = []
    for sink_id, record in sorted(state.items()):
        lag = _age_seconds(record.get("pending_since"), now) if record.get("pending_since") else 0.0
        report.append(
            {
                "sink_id": sink_id,
                "seq": record.get("seq"),
                "delivered_at": record.get("delivered_at"),
                "error": record.get("error"),
                "error_at": record.get("error_at"),
                "lag_seconds": lag,
                "degraded": bool(record.get("degraded")),
            }
        )
    for sink in status.sinks():
        if sink.sink_id not in state and not sink.ok:
            report.append(
                {
                    "sink_id": sink.sink_id,
                    "seq": None,
                    "delivered_at": None,
                    "error": sink.error,
                    "error_at": sink.error_at,
                    "lag_seconds": None,
                    "degraded": False,
                }
            )
    return report
