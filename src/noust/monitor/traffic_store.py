# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tables of a site's traffic by the hour, in the metrics database (checklist G-08).

Two tables beside the metrics: ``traffic_hourly`` (one row per application and
hour: requests, how they ended, bytes) and ``traffic_top`` (the most frequent
paths, networks, status codes and referrers of that hour). They live in
``metrics.db`` and not in the store because their volume is a metrics volume: a
busy server is a few thousand rows a day, and they are dropped by the same
retention.

The totals are kept as long as the hourly metrics (``metrics.retention_days``);
the rankings are kept :data:`TOP_RETENTION_DAYS`, because the question "what was
asked for most" is about a week or a month, not about last year.

Functions over a connection rather than a class: :class:`~noust.monitor.timeseries.MetricsStore`
owns the connection and the transaction rules.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from noust.monitor.traffic_digest import DIMENSIONS, HourCounts

#: Days a ranking is kept.
TOP_RETENTION_DAYS = 90

#: Most keys a ranking answers with.
MAX_TOP = 50

#: A window of at most this many hours is answered by the hour; longer, by the day.
HOURLY_UP_TO = 48


def create_tables(conn: sqlite3.Connection) -> None:
    """
    Create the tables.

    Args:
        conn: The connection, inside the schema transaction.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS traffic_hourly (
            app TEXT NOT NULL,
            hour INTEGER NOT NULL,
            requests INTEGER NOT NULL,
            s2xx INTEGER NOT NULL,
            s3xx INTEGER NOT NULL,
            s4xx INTEGER NOT NULL,
            s5xx INTEGER NOT NULL,
            bytes INTEGER NOT NULL,
            PRIMARY KEY (app, hour)
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS traffic_top (
            app TEXT NOT NULL,
            hour INTEGER NOT NULL,
            dimension TEXT NOT NULL,
            key TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (app, hour, dimension, key)
        ) WITHOUT ROWID
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS traffic_top_by_hour ON traffic_top (hour)")


def add(conn: sqlite3.Connection, app: str, hours: Mapping[int, HourCounts]) -> None:
    """
    Add what some lines said to the hours they belong to.

    Counts are added to what the hour already has: a log is read in pieces, and
    the end of one hour arrives in several.

    Args:
        conn: The connection.
        app: The application.
        hours: Counts by hour.
    """
    with conn:
        for hour, counts in hours.items():
            conn.execute(
                """
                INSERT INTO traffic_hourly (app, hour, requests, s2xx, s3xx, s4xx, s5xx, bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (app, hour) DO UPDATE SET
                    requests = requests + excluded.requests,
                    s2xx = s2xx + excluded.s2xx, s3xx = s3xx + excluded.s3xx,
                    s4xx = s4xx + excluded.s4xx, s5xx = s5xx + excluded.s5xx,
                    bytes = bytes + excluded.bytes
                """,
                (
                    app,
                    hour,
                    counts.requests,
                    counts.classes[2],
                    counts.classes[3],
                    counts.classes[4],
                    counts.classes[5],
                    counts.bytes,
                ),
            )
            conn.executemany(
                """
                INSERT INTO traffic_top (app, hour, dimension, key, count) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (app, hour, dimension, key) DO UPDATE SET count = count + excluded.count
                """,
                [
                    (app, hour, dimension, key, count)
                    for dimension in DIMENSIONS
                    for key, count in counts.keys[dimension].items()
                ],
            )


def purge(conn: sqlite3.Connection, now: int, retention_days: int) -> int:
    """
    Drop what is past its retention, and the long tail of each hour's rankings.

    Args:
        conn: The connection, inside the retention transaction.
        now: Epoch seconds.
        retention_days: How long the totals are kept.

    Returns:
        How many rows went.
    """
    dropped = conn.execute(
        "DELETE FROM traffic_hourly WHERE hour < ?", (now - retention_days * 86_400,)
    ).rowcount
    dropped += conn.execute(
        "DELETE FROM traffic_top WHERE hour < ?", (now - TOP_RETENTION_DAYS * 86_400,)
    ).rowcount
    return int(dropped)


@dataclass(frozen=True)
class TrafficPoint:
    """
    One hour (or day) of a site's traffic.

    Attributes:
        ts: Where it starts, epoch seconds UTC.
        requests: Requests.
        s2xx: Answered 2xx.
        s3xx: Answered 3xx.
        s4xx: Answered 4xx.
        s5xx: Answered 5xx.
        bytes: Bytes of bodies served.
    """

    ts: int
    requests: int
    s2xx: int
    s3xx: int
    s4xx: int
    s5xx: int
    bytes: int


@dataclass
class TrafficSummary:
    """
    What a site's traffic was over a window.

    Attributes:
        start: Where the window starts.
        end: Where it ends.
        step: Seconds per point: an hour or a day.
        points: The traffic by step, oldest first, every step present.
        totals: The same summed over the window.
        top: For each of :data:`~noust.monitor.traffic_digest.DIMENSIONS`, the
            keys seen most, as ``(key, count)``.
    """

    start: int
    end: int
    step: int
    points: list[TrafficPoint] = field(default_factory=list)
    totals: TrafficPoint = field(default_factory=lambda: TrafficPoint(0, 0, 0, 0, 0, 0, 0))
    top: dict[str, list[tuple[str, int]]] = field(default_factory=dict)


def summary(
    conn: sqlite3.Connection, app: str, start: int, end: int, *, top: int = 10
) -> TrafficSummary:
    """
    Read a site's traffic over a window.

    Args:
        conn: The connection.
        app: The application.
        start: Start of the window, epoch seconds.
        end: End of the window.
        top: How many keys each ranking answers with, at most :data:`MAX_TOP`.

    Returns:
        The points (hourly for a window up to :data:`HOURLY_UP_TO` hours, daily
        beyond), the totals and the rankings. A step with nothing in it is a
        point of zeros, so a chart draws a gap as a gap of the right width.
    """
    top = max(1, min(top, MAX_TOP))
    hours = (end - start) / 3600
    step = 3600 if hours <= HOURLY_UP_TO else 86_400
    first = start - start % step
    rows = conn.execute(
        "SELECT hour, requests, s2xx, s3xx, s4xx, s5xx, bytes FROM traffic_hourly "
        "WHERE app = ? AND hour >= ? AND hour < ? ORDER BY hour",
        (app, start - start % 3600, end),
    ).fetchall()
    folded: dict[int, list[int]] = {}
    for hour, *values in rows:
        slot = hour - hour % step
        into = folded.setdefault(slot, [0] * 6)
        for index, value in enumerate(values):
            into[index] += int(value)
    result = TrafficSummary(start=first, end=end, step=step)
    total = [0] * 6
    ts = first
    while ts < end:
        values = folded.get(ts, [0] * 6)
        result.points.append(TrafficPoint(ts, *values))
        for index, value in enumerate(values):
            total[index] += value
        ts += step
    result.totals = TrafficPoint(first, *total)
    for dimension in DIMENSIONS:
        ranked = conn.execute(
            "SELECT key, SUM(count) AS n FROM traffic_top "
            "WHERE app = ? AND dimension = ? AND hour >= ? AND hour < ? "
            "GROUP BY key ORDER BY n DESC, key LIMIT ?",
            (app, dimension, start - start % 3600, end, top),
        ).fetchall()
        result.top[dimension] = [(str(key), int(count)) for key, count in ranked]
    return result
