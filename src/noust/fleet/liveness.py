# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When a server counts as down, how long to wait before asking again, and when to say nothing.

Every look at a node (a person's fleet page, the reachability probe) ends in
:meth:`~noust.fleet.aggregate.Aggregator._record_reachability`, which tells this module
whether it answered. What this module decides, and keeps in the store so a restart of the
console neither forgets an outage nor announces it a second time:

- **A threshold.** One silence is a blip of the tunnel. A server is declared down only after
  :data:`UNREACHABLE_THRESHOLD` silences, counted at most once per :data:`COUNT_EVERY_SECONDS`
  (a person reloading the page ten times in ten seconds is one look, not ten), and only once
  the silence has lasted :data:`UNREACHABLE_GRACE_SECONDS`.
- **A wait that grows.** A server that keeps being down is asked after 1 cycle, then 3, 6 and
  12 (:data:`BACKOFF_CYCLES`), not every half minute for ever: the probe asks
  :meth:`Liveness.due` which servers are worth a look now. The first answer resets the count to
  nothing (Coolify's counter of unreachable servers never went back to zero, #11416).
- **A switch.** An operator who is working on a server pauses its monitoring: no probe, no
  announcement, whatever it does, until they resume it. Pausing is recorded with who and why.
- **Quiet.** The outage is announced once, when it is declared, and its end only if the outage
  was announced. A restart in the middle of an outage announces neither again.

Time here is wall-clock seconds: the state outlives the process, and a monotonic clock does not.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore, get_store

logger = logging.getLogger(__name__)

#: Seconds between the reachability probe's rounds: the cycle the wait after a silence is counted in.
PROBE_INTERVAL_SECONDS = 30.0

#: Silences needed before an outage is declared.
UNREACHABLE_THRESHOLD = 2

#: How long the silence must have lasted before it is announced: a blip is not an outage.
UNREACHABLE_GRACE_SECONDS = 120.0

#: A silence counts at most once in this many seconds.
COUNT_EVERY_SECONDS = 20.0

#: A silence nobody followed up for this long, before any outage was declared, was a blip that
#: ended unseen; the count starts again rather than pairing it with a much later one.
STALE_AFTER_SECONDS = 600.0

#: How many probe cycles to wait before asking a server again, by how many silences it has had
#: (the last entry repeats). 1 cycle is the probe's interval.
BACKOFF_CYCLES = (1, 1, 1, 3, 6, 12)


def cycles_after(failures: int) -> int:
    """
    Say how many probe cycles to wait before asking a silent server again.

    Args:
        failures: How many silences it has had in a row.

    Returns:
        1 until the outage has been declared, then 3, 6 and 12.
    """
    if failures <= 0:
        return 1
    return BACKOFF_CYCLES[min(failures, len(BACKOFF_CYCLES)) - 1]


@dataclass(frozen=True)
class LivenessState:
    """
    What is known about one server's silence.

    Attributes:
        node: The server.
        failures: Silences counted since it last answered.
        since: When the first of them was, epoch seconds; None when it answers.
        announced: The outage was announced.
        next_probe_at: When it is worth asking again; None to ask at the next cycle.
        last_counted_at: When the last silence was counted.
        last_error: What the last silence said, in the node's or ssh's words.
        paused: Monitoring is switched off.
        paused_reason: Why, in the operator's words.
        paused_by: Who switched it off.
        paused_at: When, ISO 8601 UTC.
    """

    node: str
    failures: int = 0
    since: float | None = None
    announced: bool = False
    next_probe_at: float | None = None
    last_counted_at: float | None = None
    last_error: str | None = None
    paused: bool = False
    paused_reason: str | None = None
    paused_by: str | None = None
    paused_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The state as the API shows it.
        """
        return {
            "failures": self.failures,
            "since": (
                datetime.fromtimestamp(self.since, tz=timezone.utc).isoformat()
                if self.since is not None
                else None
            ),
            "announced": self.announced,
            "paused": self.paused,
            "paused_reason": self.paused_reason,
            "paused_by": self.paused_by,
            "paused_at": self.paused_at,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class Verdict:
    """
    What a look changed.

    Attributes:
        announce_down: The outage was just declared: tell the operator once.
        announce_recovered: The server answers again after an outage that was announced.
        down_for: How long the outage lasted, seconds, when it ended.
        state: The state after the look.
    """

    state: LivenessState
    announce_down: bool = False
    announce_recovered: bool = False
    down_for: float = 0.0


def _now_iso() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


class Liveness:
    """
    Read and write the liveness of the servers of one central.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    # -- Reading --------------------------------------------------------------

    def get(self, node: str) -> LivenessState:
        """
        Read one server's state.

        Args:
            node: The server.

        Returns:
            Its state; a clean one when nothing is recorded.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM node_liveness WHERE node = ?", (node,))
            .fetchone()
        )
        return _state(row) if row is not None else LivenessState(node=node)

    def all(self) -> dict[str, LivenessState]:
        """
        Returns:
            Every recorded state, by server.
        """
        rows = self.store._get_connection().execute("SELECT * FROM node_liveness").fetchall()
        return {str(row["node"]): _state(row) for row in rows}

    def due(self, nodes: Iterable[str], now: float) -> list[str]:
        """
        Pick the servers worth asking now.

        Args:
            nodes: Every server there is.
            now: Wall-clock seconds.

        Returns:
            Those not paused and not waiting out their backoff, in the order given.
        """
        states = self.all()
        chosen = []
        for node in nodes:
            state = states.get(node)
            if state is None:
                chosen.append(node)
            elif not state.paused and (state.next_probe_at is None or state.next_probe_at <= now):
                chosen.append(node)
        return chosen

    # -- Looking ----------------------------------------------------------------

    def failed(
        self,
        node: str,
        now: float,
        *,
        error: str | None = None,
        interval: float = PROBE_INTERVAL_SECONDS,
    ) -> Verdict:
        """
        Count a silence, and decide whether the outage is declared now.

        Args:
            node: The server that did not answer.
            now: Wall-clock seconds.
            error: What the silence said.
            interval: Seconds between probe cycles, for the backoff.

        Returns:
            The verdict: ``announce_down`` once, when the threshold and the grace are met.
        """
        state = self.get(node)
        if state.paused:
            return Verdict(state=state)
        if (
            not state.announced
            and state.last_counted_at is not None
            and now - state.last_counted_at > STALE_AFTER_SECONDS
        ):
            # A lone silence long ago is a blip that ended: it does not pair with this one.
            state = LivenessState(node=node)
        counted = (
            state.last_counted_at is None or now - state.last_counted_at >= COUNT_EVERY_SECONDS
        )
        failures = state.failures + (1 if counted else 0)
        since = state.since if state.since is not None else now
        announce = (
            not state.announced
            and failures >= UNREACHABLE_THRESHOLD
            and now - since >= UNREACHABLE_GRACE_SECONDS
        )
        updated = LivenessState(
            node=node,
            failures=failures,
            since=since,
            announced=state.announced or announce,
            next_probe_at=now + interval * cycles_after(failures)
            if counted
            else state.next_probe_at,
            last_counted_at=now if counted else state.last_counted_at,
            last_error=(error or state.last_error or None),
        )
        self._save(updated)
        return Verdict(state=updated, announce_down=announce)

    def answered(self, node: str, now: float) -> Verdict:
        """
        Note that a server answered: the count goes back to nothing.

        Args:
            node: The server.
            now: Wall-clock seconds.

        Returns:
            The verdict: ``announce_recovered`` when an outage that was announced ends.
        """
        state = self.get(node)
        if state.paused:
            # A paused server's silences are not counted, so there is nothing to reset
            # but the pause stays: only the operator resumes it.
            return Verdict(state=state)
        if state.failures == 0 and not state.announced and state.since is None:
            return Verdict(state=state)
        recovered = state.announced
        down_for = max(0.0, now - state.since) if state.since is not None else 0.0
        clean = LivenessState(node=node)
        self._save(clean)
        return Verdict(state=clean, announce_recovered=recovered, down_for=down_for)

    def forget_outage(self, node: str) -> None:
        """
        Drop an outage that was expected (a reboot the central asked for).

        Args:
            node: The server.
        """
        state = self.get(node)
        if state.failures or state.announced or state.since is not None:
            self._save(
                LivenessState(
                    node=node,
                    paused=state.paused,
                    paused_reason=state.paused_reason,
                    paused_by=state.paused_by,
                    paused_at=state.paused_at,
                )
            )

    # -- The operator -------------------------------------------------------------

    def pause(
        self, node: str, *, reason: str | None = None, actor: str | None = None
    ) -> LivenessState:
        """
        Switch a server's monitoring off, for maintenance.

        Args:
            node: The server.
            reason: Why, in a line.
            actor: Who.

        Returns:
            Its state; the outage in progress, if any, is dropped.
        """
        text = (reason or "").strip()[:200] or None
        state = LivenessState(
            node=node, paused=True, paused_reason=text, paused_by=actor, paused_at=_now_iso()
        )
        self._save(state)
        return state

    def resume(self, node: str) -> LivenessState:
        """
        Switch a server's monitoring back on, with a clean count.

        Args:
            node: The server.

        Returns:
            Its state.
        """
        state = LivenessState(node=node)
        self._save(state)
        return state

    def forget(self, node: str) -> None:
        """
        Forget a server that is no longer in the fleet.

        Args:
            node: The server.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM node_liveness WHERE node = ?", (node,))

    # -- Storage ------------------------------------------------------------------

    def _save(self, state: LivenessState) -> None:
        """
        Write a state; a clean one is no row at all.

        Args:
            state: The state.
        """
        clean = (
            state.failures == 0
            and state.since is None
            and not state.announced
            and not state.paused
            and state.next_probe_at is None
        )
        try:
            with self.store._transaction() as cursor:
                if clean:
                    cursor.execute("DELETE FROM node_liveness WHERE node = ?", (state.node,))
                    return
                cursor.execute(
                    "INSERT INTO node_liveness (node, failures, down_since, announced, next_probe_at, "
                    "last_counted_at, last_error, paused, paused_reason, paused_by, paused_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(node) DO UPDATE SET "
                    "failures = excluded.failures, down_since = excluded.down_since, "
                    "announced = excluded.announced, next_probe_at = excluded.next_probe_at, "
                    "last_counted_at = excluded.last_counted_at, last_error = excluded.last_error, "
                    "paused = excluded.paused, paused_reason = excluded.paused_reason, "
                    "paused_by = excluded.paused_by, paused_at = excluded.paused_at",
                    (
                        state.node,
                        state.failures,
                        state.since,
                        int(state.announced),
                        state.next_probe_at,
                        state.last_counted_at,
                        (state.last_error or "")[:2000] or None,
                        int(state.paused),
                        state.paused_reason,
                        state.paused_by,
                        state.paused_at,
                    ),
                )
        except sqlite3.Error as exc:
            # A look is never refused for want of somewhere to write its count.
            logger.warning("Could not record the liveness of %s: %s", state.node, exc)


def _state(row: Any) -> LivenessState:
    """
    Build a state out of its row.

    Args:
        row: A ``node_liveness`` row.

    Returns:
        The state.
    """
    return LivenessState(
        node=str(row["node"]),
        failures=int(row["failures"]),
        since=row["down_since"],
        announced=bool(row["announced"]),
        next_probe_at=row["next_probe_at"],
        last_counted_at=row["last_counted_at"],
        last_error=row["last_error"],
        paused=bool(row["paused"]),
        paused_reason=row["paused_reason"],
        paused_by=row["paused_by"],
        paused_at=row["paused_at"],
    )
