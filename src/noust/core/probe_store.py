# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The probes, their state, their results and their incidents, in the store (checklist G-17).

A probe asks an address, from where Noust runs, what a visitor would ask it: does
it answer, with a status that means well, in time, and (if the probe says so)
with a word the page must contain. :class:`ProbeStore` is the only reader and
writer of the tables (store v14, the NOTIFY fragment). The CLI, the API, the
monitor and the status page all go through it, so a probe is validated in one
place (:func:`validate_probe`) and a result is one transaction.

Three tables beside the probes themselves:

``probe_state``
    Where each probe stands now: ``unknown`` (never answered, or never asked),
    ``up`` or ``down``, since when, how many answers in a row were bad and what
    the last one said.
``probe_results``
    The raw answers, kept :data:`RAW_KEEP_DAYS` days (an hour's worth of charts).
``probe_daily``
    A row per probe and day: checks, how many were good, the latency's sum and
    maximum. Kept ``DAILY_KEEP_DAYS`` and what uptime is read from: "99.94% over
    ninety days" must not depend on rows that are about to be purged.
``probe_incidents``
    Each stretch a probe was down: when it began, when it ended, and what the
    first bad answer said.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: Probes a server may have: a handful of addresses, not a monitoring product.
MAX_PROBES = 50

#: A probe is named like an application's slug.
NAME_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,39}")

METHODS = ("GET", "HEAD")

#: Seconds between two asks, and how long one gets to answer.
MIN_INTERVAL_S = 30
MAX_INTERVAL_S = 3_600
DEFAULT_INTERVAL_S = 60
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 60
DEFAULT_TIMEOUT_S = 10

#: Bad answers in a row before an address is called down.
MIN_FAIL_AFTER = 1
MAX_FAIL_AFTER = 10
DEFAULT_FAIL_AFTER = 3

#: Longest piece of text a page must contain, and longest title.
MAX_TEXT = 200
MAX_TITLE = 80

#: How long the raw answers, the days and the incidents are kept.
RAW_KEEP_DAYS = 7
DAILY_KEEP_DAYS = 400
INCIDENT_KEEP_DAYS = 400

STATES = ("unknown", "up", "down")

#: ``200-399`` or ``200,204,301``.
_STATUSES = re.compile(r"\d{3}(?:-\d{3})?(?:,\d{3}(?:-\d{3})?)*")


@dataclass(frozen=True)
class Probe:
    """
    One address a probe asks.

    Attributes:
        name: Its identifier.
        url: What is asked: ``http://`` or ``https://``, no credentials in it.
        method: ``GET`` or ``HEAD``.
        expect_status: The statuses that mean well: ``200-399`` or ``200,204``.
        expect_text: A word the page must contain (``GET`` only); empty for none.
        interval_s: Seconds between two asks.
        timeout_s: Seconds an answer may take.
        fail_after: Bad answers in a row that make it down.
        public: Shown on the status page.
        title: What the status page calls it; the name when empty.
        enabled: Whether it is asked.
        id: Its row; None until it is stored.
        created_at: Epoch seconds.
        updated_at: Epoch seconds.
    """

    name: str
    url: str
    method: str = "GET"
    expect_status: str = "200-399"
    expect_text: str = ""
    interval_s: int = DEFAULT_INTERVAL_S
    timeout_s: int = DEFAULT_TIMEOUT_S
    fail_after: int = DEFAULT_FAIL_AFTER
    public: bool = False
    title: str = ""
    enabled: bool = True
    id: int | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The probe as the API shows it.
        """
        return {
            "name": self.name,
            "url": self.url,
            "method": self.method,
            "expect_status": self.expect_status,
            "expect_text": self.expect_text,
            "interval_s": self.interval_s,
            "timeout_s": self.timeout_s,
            "fail_after": self.fail_after,
            "public": self.public,
            "title": self.title,
            "enabled": self.enabled,
        }

    def accepts(self, status: int) -> bool:
        """
        Args:
            status: An HTTP status.

        Returns:
            Whether the probe takes it as a good answer.
        """
        for part in self.expect_status.split(","):
            low, _, high = part.partition("-")
            if int(low) <= status <= int(high or low):
                return True
        return False


def validate_probe(probe: Probe) -> Probe:
    """
    Check a probe and normalise what can be.

    Args:
        probe: The probe as given.

    Returns:
        The probe, with the method upper-cased and the text trimmed.

    Raises:
        ValidationError: Naming the field that is not acceptable.
    """
    if not NAME_PATTERN.fullmatch(probe.name):
        raise ValidationError(
            f"Invalid probe name: {probe.name!r}",
            details="Use lower-case letters, digits and '-', starting with a letter, at most 40.",
            field="name",
        )
    parts = urlsplit(probe.url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError(
            "A probe asks an http:// or https:// address",
            details="Give the whole address, such as https://shop.example.com/health.",
            field="url",
        )
    if parts.username or parts.password:
        raise ValidationError(
            "The address of a probe has no user or password in it",
            details="A URL is written to logs and shown in lists; a secret does not belong in one.",
            field="url",
        )
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in probe.url):
        raise ValidationError(
            "The address of a probe has no spaces or control characters", field="url"
        )
    method = probe.method.strip().upper()
    if method not in METHODS:
        raise ValidationError(f"The method is one of: {', '.join(METHODS)}", field="method")
    expect = probe.expect_status.replace(" ", "")
    if not _STATUSES.fullmatch(expect) or any(
        not 100 <= int(number) <= 599 for number in re.findall(r"\d{3}", expect)
    ):
        raise ValidationError(
            "The statuses that mean well are like 200-399 or 200,204",
            details="Three-digit HTTP statuses, 100 to 599, alone or as a range.",
            field="expect_status",
        )
    for chunk in expect.split(","):
        first, _, last = chunk.partition("-")
        if last and int(last) < int(first):
            raise ValidationError(
                f"{chunk} is a range that ends before it starts", field="expect_status"
            )
    text = probe.expect_text.strip()
    if len(text) > MAX_TEXT:
        raise ValidationError(
            f"The text a page must contain is at most {MAX_TEXT} characters", field="expect_text"
        )
    if text and method != "GET":
        raise ValidationError(
            "A page can only be searched for a word with GET",
            details="HEAD returns no body.",
            field="expect_text",
        )
    for label, value, low, high in (
        ("interval_s", probe.interval_s, MIN_INTERVAL_S, MAX_INTERVAL_S),
        ("timeout_s", probe.timeout_s, MIN_TIMEOUT_S, MAX_TIMEOUT_S),
        ("fail_after", probe.fail_after, MIN_FAIL_AFTER, MAX_FAIL_AFTER),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValidationError(f"{label} is a number from {low} to {high}", field=label)
    if probe.timeout_s >= probe.interval_s:
        raise ValidationError(
            "An answer may take less than the time between two asks",
            details=f"timeout_s ({probe.timeout_s}) must be below interval_s ({probe.interval_s}).",
            field="timeout_s",
        )
    title = " ".join(probe.title.split())
    if len(title) > MAX_TITLE:
        raise ValidationError(f"The title is at most {MAX_TITLE} characters", field="title")
    return replace(
        probe,
        url=probe.url.strip(),
        method=method,
        expect_status=expect,
        expect_text=text,
        title=title,
    )


@dataclass(frozen=True)
class ProbeState:
    """
    Where a probe stands.

    Attributes:
        probe_id: The probe.
        state: ``unknown``, ``up`` or ``down``.
        since: Epoch seconds the state began.
        failures: Bad answers in a row.
        announced: Whether the current ``down`` was told to the channels.
        failing_since: When the bad answers in a row began; None while it answers.
        last_checked: When it was last asked.
        last_status: The HTTP status of the last answer, if there was one.
        last_latency_ms: How long it took.
        last_error: What was wrong with the last answer, in one line.
    """

    probe_id: int
    state: str = "unknown"
    since: float = 0.0
    failures: int = 0
    announced: bool = False
    failing_since: float | None = None
    last_checked: float | None = None
    last_status: int | None = None
    last_latency_ms: int | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The state as the API shows it.
        """
        return {
            "state": self.state,
            "since": self.since,
            "failures": self.failures,
            "last_checked": self.last_checked,
            "last_status": self.last_status,
            "last_latency_ms": self.last_latency_ms,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class Incident:
    """
    A stretch an address did not answer.

    Attributes:
        probe_id: The probe.
        started_at: Epoch seconds of the first bad answer.
        ended_at: When it answered again; None while it is down.
        reason: What the first bad answer said.
    """

    probe_id: int
    started_at: float
    ended_at: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The incident as the API shows it.
        """
        return {"started_at": self.started_at, "ended_at": self.ended_at, "reason": self.reason}


@dataclass(frozen=True)
class Uptime:
    """
    How often an address answered over a window.

    Attributes:
        checks: Times it was asked.
        good: Times it answered well.
        mean_ms: The mean latency of the good answers; None when there were none.
        max_ms: The slowest.
    """

    checks: int = 0
    good: int = 0
    mean_ms: float | None = None
    max_ms: int | None = None

    @property
    def percent(self) -> float | None:
        """Returns: Good answers as a percentage of the checks; None when never asked."""
        return None if not self.checks else round(100.0 * self.good / self.checks, 3)


@dataclass
class DayBar:
    """
    One day of a probe, for a bar of the status page.

    Attributes:
        day: Epoch seconds of the day's start (UTC).
        checks: Times asked.
        good: Times it answered well.
    """

    day: int
    checks: int = 0
    good: int = 0


def _probe(row: sqlite3.Row) -> Probe:
    """
    Args:
        row: A row of ``probes``.

    Returns:
        The probe.
    """
    return Probe(
        id=int(row["id"]),
        name=str(row["name"]),
        url=str(row["url"]),
        method=str(row["method"]),
        expect_status=str(row["expect_status"]),
        expect_text=str(row["expect_text"] or ""),
        interval_s=int(row["interval_s"]),
        timeout_s=int(row["timeout_s"]),
        fail_after=int(row["fail_after"]),
        public=bool(row["public"]),
        title=str(row["title"] or ""),
        enabled=bool(row["enabled"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _state(row: sqlite3.Row) -> ProbeState:
    """
    Args:
        row: A row of ``probe_state``.

    Returns:
        The state.
    """
    return ProbeState(
        probe_id=int(row["probe_id"]),
        state=str(row["state"]),
        since=float(row["since"]),
        failures=int(row["failures"]),
        announced=bool(row["announced"]),
        failing_since=row["failing_since"],
        last_checked=row["last_checked"],
        last_status=row["last_status"],
        last_latency_ms=row["last_latency_ms"],
        last_error=row["last_error"],
    )


class ProbeStore:
    """
    The tables of the probes.

    Every method can raise :class:`sqlite3.Error`; the monitor logs it and the
    API's boundary answers it.
    """

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
            clock: Epoch seconds. Tests move it.
        """
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store the probes live in."""
        return self._store if self._store is not None else get_store()

    # -- the probes -----------------------------------------------------------

    def probes(self) -> list[Probe]:
        """
        Returns:
            Every probe, by name.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM probes ORDER BY name")
            return [_probe(row) for row in cursor.fetchall()]

    def get(self, name: str) -> Probe | None:
        """
        Args:
            name: A probe's name.

        Returns:
            The probe, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM probes WHERE name = ?", (name,))
            row = cursor.fetchone()
        return _probe(row) if row is not None else None

    def save(self, probe: Probe, *, actor: str | None = None) -> Probe:
        """
        Create a probe or replace the one of that name.

        Replacing it asks the address afresh: what the old settings had
        decided is forgotten (a probe moved to another address is not down
        because the old one was).

        Args:
            probe: The probe.
            actor: Who saves it.

        Returns:
            The probe as stored.

        Raises:
            ValidationError: The probe is not acceptable, or there are already
                :data:`MAX_PROBES`.
        """
        checked = validate_probe(probe)
        now = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute("SELECT id, url FROM probes WHERE name = ?", (checked.name,))
            existing = cursor.fetchone()
            if existing is None:
                cursor.execute("SELECT COUNT(*) FROM probes")
                if int(cursor.fetchone()[0]) >= MAX_PROBES:
                    raise ValidationError(
                        f"A server has at most {MAX_PROBES} probes",
                        details="Remove one you no longer need.",
                    )
                cursor.execute(
                    "INSERT INTO probes (name, url, method, expect_status, expect_text, interval_s, "
                    "timeout_s, fail_after, public, title, enabled, created_at, created_by, "
                    "updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        checked.name,
                        checked.url,
                        checked.method,
                        checked.expect_status,
                        checked.expect_text,
                        checked.interval_s,
                        checked.timeout_s,
                        checked.fail_after,
                        int(checked.public),
                        checked.title,
                        int(checked.enabled),
                        now,
                        actor,
                        now,
                        actor,
                    ),
                )
                probe_id = int(cursor.lastrowid or 0)
            else:
                probe_id = int(existing["id"])
                cursor.execute(
                    "UPDATE probes SET url = ?, method = ?, expect_status = ?, expect_text = ?, "
                    "interval_s = ?, timeout_s = ?, fail_after = ?, public = ?, title = ?, "
                    "enabled = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                    (
                        checked.url,
                        checked.method,
                        checked.expect_status,
                        checked.expect_text,
                        checked.interval_s,
                        checked.timeout_s,
                        checked.fail_after,
                        int(checked.public),
                        checked.title,
                        int(checked.enabled),
                        now,
                        actor,
                        probe_id,
                    ),
                )
                if existing["url"] != checked.url or not checked.enabled:
                    # What the old address or a switched-off probe had decided is not
                    # a fact about the new one; an incident left open would never end.
                    cursor.execute("DELETE FROM probe_state WHERE probe_id = ?", (probe_id,))
                    cursor.execute(
                        "UPDATE probe_incidents SET ended_at = ? "
                        "WHERE probe_id = ? AND ended_at IS NULL",
                        (now, probe_id),
                    )
            cursor.execute("SELECT * FROM probes WHERE id = ?", (probe_id,))
            return _probe(cursor.fetchone())

    def set_enabled(self, name: str, enabled: bool, *, actor: str | None = None) -> Probe | None:
        """
        Turn a probe on or off.

        Args:
            name: The probe.
            enabled: Whether it is asked.
            actor: Who does it.

        Returns:
            The probe as it is now; None when there is none of that name.
        """
        probe = self.get(name)
        if probe is None:
            return None
        return self.save(replace(probe, enabled=enabled), actor=actor)

    def remove(self, name: str) -> bool:
        """
        Remove a probe with its state, results and incidents.

        Args:
            name: The probe.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM probes WHERE name = ?", (name,))
            return cursor.rowcount > 0

    # -- state, results, incidents ---------------------------------------------

    def state(self, probe_id: int) -> ProbeState:
        """
        Args:
            probe_id: The probe.

        Returns:
            Where it stands; ``unknown`` when it was never asked.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM probe_state WHERE probe_id = ?", (probe_id,))
            row = cursor.fetchone()
        return _state(row) if row is not None else ProbeState(probe_id=probe_id)

    def states(self) -> dict[int, ProbeState]:
        """
        Returns:
            The state of every probe that was asked, by probe id.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM probe_state")
            return {int(row["probe_id"]): _state(row) for row in cursor.fetchall()}

    def put_state(self, state: ProbeState) -> None:
        """
        Write where a probe stands.

        Args:
            state: The state.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO probe_state (probe_id, state, since, failures, announced, "
                "failing_since, last_checked, last_status, last_latency_ms, last_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(probe_id) DO UPDATE SET state = excluded.state, "
                "since = excluded.since, failures = excluded.failures, "
                "announced = excluded.announced, failing_since = excluded.failing_since, "
                "last_checked = excluded.last_checked, "
                "last_status = excluded.last_status, last_latency_ms = excluded.last_latency_ms, "
                "last_error = excluded.last_error",
                (
                    state.probe_id,
                    state.state,
                    state.since,
                    state.failures,
                    int(state.announced),
                    state.failing_since,
                    state.last_checked,
                    state.last_status,
                    state.last_latency_ms,
                    state.last_error,
                ),
            )

    def record_result(
        self,
        probe_id: int,
        *,
        ok: bool,
        status: int | None,
        latency_ms: int | None,
        error: str | None,
        at: float | None = None,
    ) -> None:
        """
        Keep one answer, and fold it into its day.

        Args:
            probe_id: The probe.
            ok: Whether the answer was good.
            status: The HTTP status, if there was one.
            latency_ms: How long it took.
            error: What was wrong, in one line.
            at: Epoch seconds; now when None.
        """
        moment = self._clock() if at is None else at
        day = int(moment) - int(moment) % 86_400
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO probe_results (probe_id, ts, ok, status, latency_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (probe_id, moment, int(ok), status, latency_ms, error),
            )
            cursor.execute(
                "INSERT INTO probe_daily (probe_id, day, checks, good, latency_sum, latency_max) "
                "VALUES (?, ?, 1, ?, ?, ?) "
                "ON CONFLICT(probe_id, day) DO UPDATE SET checks = checks + 1, "
                "good = good + excluded.good, "
                "latency_sum = latency_sum + excluded.latency_sum, "
                "latency_max = MAX(latency_max, excluded.latency_max)",
                (
                    probe_id,
                    day,
                    int(ok),
                    latency_ms if ok and latency_ms is not None else 0,
                    latency_ms if ok and latency_ms is not None else 0,
                ),
            )

    def open_incident(self, probe_id: int, started_at: float, reason: str) -> None:
        """
        Start an incident.

        Args:
            probe_id: The probe.
            started_at: Epoch seconds of the first bad answer.
            reason: What it said.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO probe_incidents (probe_id, started_at, reason) VALUES (?, ?, ?)",
                (probe_id, started_at, reason[:500]),
            )

    def close_incident(self, probe_id: int, ended_at: float) -> None:
        """
        End the incident a probe has open.

        Args:
            probe_id: The probe.
            ended_at: Epoch seconds the address answered again.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE probe_incidents SET ended_at = ? WHERE probe_id = ? AND ended_at IS NULL",
                (ended_at, probe_id),
            )

    def incidents(self, probe_id: int, *, limit: int = 20) -> list[Incident]:
        """
        Args:
            probe_id: The probe.
            limit: How many at most.

        Returns:
            Its newest incidents first.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM probe_incidents WHERE probe_id = ? ORDER BY started_at DESC LIMIT ?",
                (probe_id, limit),
            )
            return [
                Incident(
                    probe_id=int(row["probe_id"]),
                    started_at=float(row["started_at"]),
                    ended_at=row["ended_at"],
                    reason=str(row["reason"] or ""),
                )
                for row in cursor.fetchall()
            ]

    def uptime(self, probe_id: int, days: int) -> Uptime:
        """
        Args:
            probe_id: The probe.
            days: How many days back (today included).

        Returns:
            How often it answered over that window.
        """
        floor = self._day_floor(days)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT COALESCE(SUM(checks), 0) AS checks, COALESCE(SUM(good), 0) AS good, "
                "COALESCE(SUM(latency_sum), 0) AS latency, MAX(latency_max) AS slowest "
                "FROM probe_daily WHERE probe_id = ? AND day >= ?",
                (probe_id, floor),
            )
            row = cursor.fetchone()
        good = int(row["good"])
        return Uptime(
            checks=int(row["checks"]),
            good=good,
            mean_ms=round(float(row["latency"]) / good, 1) if good else None,
            max_ms=int(row["slowest"]) if row["slowest"] else None,
        )

    def days(self, probe_id: int, days: int) -> list[DayBar]:
        """
        Args:
            probe_id: The probe.
            days: How many days back (today included).

        Returns:
            One bar per day, oldest first, a day with no check included.
        """
        floor = self._day_floor(days)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT day, checks, good FROM probe_daily WHERE probe_id = ? AND day >= ?",
                (probe_id, floor),
            )
            found = {
                int(row["day"]): (int(row["checks"]), int(row["good"])) for row in cursor.fetchall()
            }
        return [
            DayBar(day=day, checks=found.get(day, (0, 0))[0], good=found.get(day, (0, 0))[1])
            for day in range(floor, floor + days * 86_400, 86_400)
        ]

    def recent(self, probe_id: int, *, limit: int = 60) -> list[dict[str, Any]]:
        """
        Args:
            probe_id: The probe.
            limit: How many at most.

        Returns:
            The newest raw answers, oldest first, for a sparkline.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT ts, ok, status, latency_ms, error FROM probe_results "
                "WHERE probe_id = ? ORDER BY ts DESC LIMIT ?",
                (probe_id, limit),
            )
            rows = cursor.fetchall()
        return [
            {
                "ts": float(row["ts"]),
                "ok": bool(row["ok"]),
                "status": row["status"],
                "latency_ms": row["latency_ms"],
                "error": row["error"],
            }
            for row in reversed(rows)
        ]

    def purge(self) -> int:
        """
        Forget what is past its retention.

        Returns:
            How many rows went.
        """
        now = self._clock()
        with self.store._transaction() as cursor:
            dropped = cursor.execute(
                "DELETE FROM probe_results WHERE ts < ?", (now - RAW_KEEP_DAYS * 86_400,)
            ).rowcount
            dropped += cursor.execute(
                "DELETE FROM probe_daily WHERE day < ?", (self._day_floor(DAILY_KEEP_DAYS),)
            ).rowcount
            dropped += cursor.execute(
                "DELETE FROM probe_incidents WHERE ended_at IS NOT NULL AND ended_at < ?",
                (now - INCIDENT_KEEP_DAYS * 86_400,),
            ).rowcount
        return int(dropped)

    def _day_floor(self, days: int) -> int:
        """
        Args:
            days: A number of days, today included.

        Returns:
            Where the oldest of them starts, epoch seconds (UTC).
        """
        today = int(self._clock()) - int(self._clock()) % 86_400
        return today - (max(1, days) - 1) * 86_400
