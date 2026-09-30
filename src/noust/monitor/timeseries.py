"""
RRD-style persistence for the metric charts.

A collector samples the machine and every application every five seconds; the
console asks for windows from an hour to more than a year. Keeping every raw
sample for that long would be millions of rows per metric, almost all of it
invisible at chart resolution, so this store keeps four tiers the way RRDtool
does, each one holding **both the mean and the maximum** of its buckets (a five
minute CPU spike must survive the hourly tier, and a mean alone erases it):

============  ========  ==============================  =====================
tier          step      kept for                        answers a window of
============  ========  ==============================  =====================
``raw``       5 s       2 hours                         up to ~2 h
``1m``        1 minute  26 hours                        up to ~1 day
``10m``       10 min    8 days                          up to ~1 week
``1h``        1 hour    400 days (``metrics.retention_days``)  a month and beyond
============  ========  ==============================  =====================

Unlike a plain roll-up that moves data down and deletes it, every tier is fed
continuously as its buckets complete and each keeps its own retention, so a
window is answered from **one** tier, without stitching. That is what lets the
chart draw the window that was asked for at the resolution it says.

:meth:`MetricsStore.query_range` is the one read: it takes several metrics at
once, picks the tier for the window, returns a regular grid over the requested
``[start, end]`` with ``None`` where nothing was recorded (a gap is a gap, never
an interpolated line), and names the tier it actually read. When the preferred
tier does not reach back to the start of the window (a store upgraded from an
older version, whose finer tiers began later) it reads the next coarser tier
that does, and says so. Buckets that have not been consolidated yet (the last
minute, the last hour) are filled from the raw tier, so the right edge of a
day-wide chart is never blank.

The database is a sibling of ``observations.db`` and follows its concurrency
model: one connection per thread and WAL. Two processes may hold it - the
``noust-monitor`` daemon that samples and the console that reads - which is why
it also carries the **collector lease**: a single row saying who samples, so two
collectors never write the same series (:meth:`MetricsStore.acquire_collector`).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from noust.core import paths
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger

#: Where the database lives when Noust is installed system-wide.
SYSTEM_DB_PATH = paths.state_dir() / "metrics.db"

#: Fallback for an unprivileged run, so a developer never writes to /var/lib.
USER_DB_RELATIVE_PATH = Path(".local/share") / paths.NAME / "metrics.db"

#: A metric write or read must never block for long on the database.
BUSY_TIMEOUT = 10

#: Bumped when a table or column is added; stored in ``PRAGMA user_version``.
SCHEMA_VERSION = 2

#: Default days the hourly tier is kept. A year and a bit, so a year-over-year
#: comparison and an ENS audit window both fit; ``metrics.retention_days``
#: changes it, and the cost of the extra years is 24 rows per metric per day.
DEFAULT_RETENTION_DAYS = 400

#: Smallest and largest ``metrics.retention_days`` honoured: below a month the
#: 30-day window would be permanently short, above ten years a typo is the
#: likelier explanation.
MIN_RETENTION_DAYS = 35
MAX_RETENTION_DAYS = 3_650

#: Most points a series of a range query carries. A chart has about one pixel
#: per point, and a response of this size is a few tens of kilobytes per series.
MAX_RANGE_POINTS = 1_500

#: Most metrics one range query may ask for.
MAX_RANGE_SERIES = 40

#: Widest range a query may ask for, whatever the retention. Twice the largest
#: retention, so a window can always be asked over a whole (large) history.
MAX_RANGE_SECONDS = 2 * MAX_RETENTION_DAYS * 86_400

#: Default cap on points the legacy single-metric read returns.
DEFAULT_MAX_POINTS = 400

#: How long the collector lease survives without a heartbeat, in intervals.
LEASE_MISSED_TICKS = 3

#: The shortest a lease survives, so a five second interval is not a fifteen
#: second window in which a restart would look like a second collector.
LEASE_MIN_SECONDS = 20.0


@dataclass(frozen=True)
class Tier:
    """
    One consolidation tier.

    Attributes:
        name: The label a query response carries as ``resolution``.
        step: Seconds per bucket.
        retention: Seconds a bucket is kept, or None when configurable.
        resolution: The value of ``consolidated.resolution`` (0 for raw).
    """

    name: str
    step: int
    retention: int | None
    resolution: int


RAW = Tier("raw", 5, 2 * 3_600, 0)
MINUTE = Tier("1m", 60, 26 * 3_600, 60)
TEN_MINUTES = Tier("10m", 600, 8 * 86_400, 600)
HOUR = Tier("1h", 3_600, None, 3_600)

#: Finest first.
TIERS: tuple[Tier, ...] = (RAW, MINUTE, TEN_MINUTES, HOUR)

#: The tiers that are aggregated from a finer one, with what feeds each.
_ROLLED: tuple[tuple[Tier, Tier], ...] = ((MINUTE, RAW), (TEN_MINUTES, MINUTE), (HOUR, TEN_MINUTES))

# Kept for callers that predate the four tiers.
RAW_RETENTION_SECONDS = RAW.retention or 0
MINUTE_RESOLUTION = MINUTE.resolution
MINUTE_RETENTION_SECONDS = MINUTE.retention or 0
HOUR_RESOLUTION = HOUR.resolution

#: Upsert rather than fail on a duplicate second: a collector that lands twice
#: on the same timestamp (clock step, NTP slew) is reporting a gauge, and the
#: newer reading is the truer one.
_INSERT_SAMPLE_SQL = """
    INSERT INTO samples (metric, ts, value)
    VALUES (?, ?, ?)
    ON CONFLICT (metric, ts) DO UPDATE SET value = excluded.value
"""

#: The newest reading per metric, so "what is it now" is a primary-key read and
#: not a scan of two hours of samples. A late write never moves it backwards.
_UPSERT_LATEST_SQL = """
    INSERT INTO latest (metric, ts, value)
    VALUES (?, ?, ?)
    ON CONFLICT (metric) DO UPDATE SET ts = excluded.ts, value = excluded.value
    WHERE excluded.ts >= latest.ts
"""

#: Aggregate complete buckets of a finer tier into the destination tier. The
#: caller aligns :cutoff to a destination bucket boundary, so every aggregated
#: bucket is complete; DO NOTHING keeps the existing row should a
#: late-recorded sample try to rebuild one.
_ROLL_FROM_RAW_SQL = """
    INSERT INTO consolidated (metric, resolution, ts, value, max_value)
    SELECT metric, :to_resolution, (ts / :to_resolution) * :to_resolution,
           AVG(value), MAX(value)
    FROM samples
    WHERE ts >= :floor AND ts < :cutoff
    GROUP BY metric, ts / :to_resolution
    ON CONFLICT (metric, resolution, ts) DO NOTHING
"""

_ROLL_FROM_TIER_SQL = """
    INSERT INTO consolidated (metric, resolution, ts, value, max_value)
    SELECT metric, :to_resolution, (ts / :to_resolution) * :to_resolution,
           AVG(value), MAX(COALESCE(max_value, value))
    FROM consolidated
    WHERE resolution = :from_resolution AND ts >= :floor AND ts < :cutoff
    GROUP BY metric, ts / :to_resolution
    ON CONFLICT (metric, resolution, ts) DO NOTHING
"""

#: Metrics whose ceiling is another metric: the chart of used memory is drawn
#: against total memory. The total is a property of the series, not a series of
#: its own on the wire.
CEILINGS: dict[str, str] = {
    "mem.used_bytes": "mem.total_bytes",
    "swap.used_bytes": "swap.total_bytes",
    "disk.used_bytes": "disk.total_bytes",
}


def default_metrics_db_path() -> Path:
    """
    Choose the database location for the current process.

    Resolved on every call rather than at import time, so a changed HOME (a
    test sandbox, a service switching user) is honoured.

    Returns:
        The system path when its directory is writable, the user path otherwise.
    """
    system_dir = SYSTEM_DB_PATH.parent
    if system_dir.is_dir() and os.access(system_dir, os.W_OK):
        return SYSTEM_DB_PATH
    return Path.home() / USER_DB_RELATIVE_PATH


def _marks(count: int) -> str:
    """
    Build the placeholders of an ``IN (...)`` list.

    Args:
        count: How many values will be bound.

    Returns:
        ``?,?,?`` with that many markers: the values themselves are always
        bound, never interpolated.
    """
    return ",".join("?" * count)


def resolve_retention_days(value: object) -> int:
    """
    Turn a configured ``metrics.retention_days`` into a safe number of days.

    Args:
        value: What the configuration holds; anything that is not a whole
            number reads as the default.

    Returns:
        Days, clamped to :data:`MIN_RETENTION_DAYS` .. :data:`MAX_RETENTION_DAYS`.
    """
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, days))


@dataclass(frozen=True)
class RangeSeries:
    """
    One metric of a range read.

    Attributes:
        metric: Metric name.
        points: ``(ts, mean, maximum)`` for every cell of the grid, oldest
            first; ``mean`` and ``maximum`` are both None for a cell where
            nothing was recorded.
        ceiling: The metric's ceiling (total memory for used memory), or None.
    """

    metric: str
    points: list[tuple[int, float | None, float | None]]
    ceiling: float | None = None


@dataclass(frozen=True)
class RangeResult:
    """
    What :meth:`MetricsStore.query_range` read.

    Attributes:
        start: Start of the requested domain, epoch seconds.
        end: End of the requested domain.
        step: Seconds per cell of the grid.
        resolution: The tier the data was read from (``raw``, ``1m``, ``10m``
            or ``1h``). A cell wider than the tier is a mean over several of
            its buckets; ``step`` says how wide.
        first_sample_at: The oldest sample any of the metrics has, in any
            tier, or None when there is nothing: where "history since" starts.
        last_sample_at: The newest sample of any of the metrics, or None.
        series: One entry per requested metric, in the order asked.
    """

    start: int
    end: int
    step: int
    resolution: str
    first_sample_at: int | None
    last_sample_at: int | None
    series: list[RangeSeries] = field(default_factory=list)


@dataclass(frozen=True)
class CollectorLease:
    """
    The row that says who is sampling.

    Attributes:
        owner: Unique id of the collector instance holding it.
        kind: ``daemon`` (the ``noust-monitor`` process) or ``console`` (the
            web process sampling because no daemon does).
        pid: The holder's process id.
        started_at: When it took the lease.
        heartbeat_at: The last tick it wrote, epoch seconds.
        interval_s: Seconds between its ticks.
        ticks: Ticks it has taken since it started.
        last_error: The newest operational failure it logged, verbatim.
        last_error_at: When that happened.
    """

    owner: str
    kind: str
    pid: int
    started_at: int
    heartbeat_at: int
    interval_s: float
    ticks: int = 0
    last_error: str | None = None
    last_error_at: int | None = None

    def is_live(self, now: float) -> bool:
        """
        Say whether the holder is still ticking.

        Args:
            now: The current time, epoch seconds.

        Returns:
            True while its last heartbeat is younger than the lease timeout.
        """
        return now - self.heartbeat_at <= lease_timeout(self.interval_s)


#: Who may take a lease from whom. The daemon outranks the console: when it
#: starts, the console's collector notices it lost the lease and stops.
_KIND_PRIORITY: dict[str, int] = {"console": 1, "daemon": 2}


def lease_timeout(interval_s: float) -> float:
    """
    Say how long a lease survives without a heartbeat.

    Args:
        interval_s: Seconds between the holder's ticks.

    Returns:
        Seconds.
    """
    return max(LEASE_MIN_SECONDS, LEASE_MISSED_TICKS * float(interval_s))


class MetricsStore:
    """
    SQLite time series with RRD-style tiers.

    One connection per thread, because the collector writes the same file the
    web handlers read.
    """

    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        verbose: bool = False,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        """
        Args:
            db_path: Database file. Defaults to :func:`default_metrics_db_path`.
            clock: Source of the current time in epoch seconds. Injected so
                tests never depend on the wall clock.
            verbose: Enable verbose logging.
            retention_days: Days the hourly tier is kept; see
                :func:`resolve_retention_days`.
        """
        self.logger = Logger(verbose=verbose)
        self.db_path = db_path or default_metrics_db_path()
        self._clock = clock
        self._local = threading.local()
        self.retention_days = resolve_retention_days(retention_days)
        self._init_db()

    # ------------------------------------------------------------ connection

    def _get_connection(self) -> sqlite3.Connection:
        """
        Return this thread's connection, opening it on first use.

        Returns:
            An open SQLite connection with row access by name.
        """
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self.db_path), timeout=BUSY_TIMEOUT)
            connection.row_factory = sqlite3.Row
            # The collector writes this file while the console reads it;
            # without WAL one of them gets "database is locked" instead of an
            # answer.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            self._local.connection = connection
        return connection

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        """
        Run a block in a transaction that takes the write lock at once.

        A lease decision (read the row, then write it) must not interleave with
        another process doing the same, and a plain deferred transaction would
        let both read before either wrote.

        Yields:
            This thread's connection, inside ``BEGIN IMMEDIATE``.
        """
        conn = self._get_connection()
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            yield conn
            conn.commit()
            committed = True
        finally:
            if not committed:
                conn.rollback()

    def close(self) -> None:
        """Close this thread's connection, if it has one."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    # ---------------------------------------------------------------- schema

    def _init_db(self) -> None:
        """Create the schema, or bring an older one up to :data:`SCHEMA_VERSION`."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._immediate() as conn:
            # WITHOUT ROWID: the primary key is the whole access pattern, and
            # a hidden rowid would double every index for nothing.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    metric TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    value REAL NOT NULL,
                    PRIMARY KEY (metric, ts)
                ) WITHOUT ROWID
                """
            )
            # max_value is NULL in rows written before schema 2, which only
            # kept means: reading them, the mean is the best answer there is.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS consolidated (
                    metric TEXT NOT NULL,
                    resolution INTEGER NOT NULL,
                    ts INTEGER NOT NULL,
                    value REAL NOT NULL,
                    max_value REAL,
                    PRIMARY KEY (metric, resolution, ts)
                ) WITHOUT ROWID
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(consolidated)")}
            if "max_value" not in columns:
                conn.execute("ALTER TABLE consolidated ADD COLUMN max_value REAL")
            # Roll-ups and retention deletes read a whole tier over a time
            # range, which the (metric, ...) primary key cannot serve.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS consolidated_by_time ON consolidated (resolution, ts)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS latest (
                    metric TEXT NOT NULL PRIMARY KEY,
                    ts INTEGER NOT NULL,
                    value REAL NOT NULL
                ) WITHOUT ROWID
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collector (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    owner TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    started_at INTEGER NOT NULL,
                    heartbeat_at INTEGER NOT NULL,
                    interval_s REAL NOT NULL,
                    ticks INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    last_error_at INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS log_cursors (
                    path TEXT NOT NULL PRIMARY KEY,
                    inode INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                ) WITHOUT ROWID
                """
            )
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version < SCHEMA_VERSION:
                # Seed the newest-reading table from what an older version left,
                # so the roll-up finds every metric on the first run.
                conn.execute(
                    """
                    INSERT OR IGNORE INTO latest (metric, ts, value)
                    SELECT s.metric, s.ts, s.value FROM samples s
                    WHERE s.ts = (SELECT MAX(ts) FROM samples WHERE metric = s.metric)
                    """
                )
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        self.logger.debug(f"Metrics store ready at {self.db_path}")

    def now(self) -> int:
        """
        Read the store's clock.

        Returns:
            The current time in epoch seconds: the injected clock, so a test
            and the code under it agree on "now".
        """
        return int(self._clock())

    # ---------------------------------------------------------------- writes

    def record(self, metric: str, value: float, *, ts: int | None = None) -> None:
        """
        Store one sample.

        Args:
            metric: Metric name, e.g. ``cpu.percent`` or ``app.example.com.mem.bytes``.
            value: The sampled value. Must be a real number: SQLite stores NaN
                as NULL, which the schema rejects, loudly.
            ts: Sample time in epoch seconds. Defaults to the injected clock.
        """
        self.record_many([(metric, value)], ts=ts)

    def record_many(self, pairs: Iterable[tuple[str, float]], *, ts: int | None = None) -> None:
        """
        Store one collector tick: many metrics, one timestamp, one transaction.

        Args:
            pairs: ``(metric, value)`` pairs sampled together.
            ts: Sample time in epoch seconds. Defaults to the injected clock.
        """
        stamp = int(self._clock()) if ts is None else int(ts)
        rows = [(metric, stamp, float(value)) for metric, value in pairs]
        if not rows:
            return
        conn = self._get_connection()
        with conn:
            conn.executemany(_INSERT_SAMPLE_SQL, rows)
            conn.executemany(_UPSERT_LATEST_SQL, rows)

    def consolidate(self, *, now: int | None = None, lookback: int | None = None) -> None:
        """
        Feed the coarser tiers from the finer ones and drop what expired.

        Every complete bucket of a tier is aggregated (mean and maximum) from
        the tier below it as soon as it completes, so each tier holds its whole
        retention on its own. Only complete buckets are aggregated, which is
        what makes a second run with the same clock a no-op. Rows past their
        tier's retention are deleted last.

        Args:
            now: The current time in epoch seconds. Defaults to the injected
                clock.
            lookback: Only aggregate source rows this many seconds back. A
                collector calls with a lookback on its timer, since older
                buckets were done by an earlier run; None (the default, and
                what a start-up catch-up uses) scans the whole source.
        """
        now_ts = int(self._clock()) if now is None else int(now)
        conn = self._get_connection()
        with conn:
            for destination, source in _ROLLED:
                cutoff = now_ts // destination.step * destination.step
                floor = (
                    0
                    if lookback is None
                    else (now_ts - lookback) // destination.step * destination.step
                )
                if source is RAW:
                    conn.execute(
                        _ROLL_FROM_RAW_SQL,
                        {
                            "to_resolution": destination.resolution,
                            "floor": floor,
                            "cutoff": cutoff,
                        },
                    )
                else:
                    conn.execute(
                        _ROLL_FROM_TIER_SQL,
                        {
                            "from_resolution": source.resolution,
                            "to_resolution": destination.resolution,
                            "floor": floor,
                            "cutoff": cutoff,
                        },
                    )

            # A tier's rows are deleted only up to a boundary of the tier
            # that consumes them, so a bucket is never cut in half.
            raw_cutoff = (now_ts - (RAW.retention or 0)) // MINUTE.step * MINUTE.step
            dropped = conn.execute("DELETE FROM samples WHERE ts < ?", (raw_cutoff,)).rowcount
            minute_cutoff = (
                (now_ts - (MINUTE.retention or 0)) // TEN_MINUTES.step * TEN_MINUTES.step
            )
            dropped += conn.execute(
                "DELETE FROM consolidated WHERE resolution = ? AND ts < ?",
                (MINUTE.resolution, minute_cutoff),
            ).rowcount
            ten_cutoff = (now_ts - (TEN_MINUTES.retention or 0)) // HOUR.step * HOUR.step
            dropped += conn.execute(
                "DELETE FROM consolidated WHERE resolution = ? AND ts < ?",
                (TEN_MINUTES.resolution, ten_cutoff),
            ).rowcount
            hour_cutoff = now_ts - self.retention_days * 86_400
            dropped += conn.execute(
                "DELETE FROM consolidated WHERE resolution = ? AND ts < ?",
                (HOUR.resolution, hour_cutoff),
            ).rowcount
            # A metric that has been silent for a day (a deleted application)
            # stops costing a row in the newest-reading table.
            conn.execute("DELETE FROM latest WHERE ts < ?", (now_ts - 86_400,))

        if dropped:
            self.logger.debug(f"Metrics retention dropped {dropped} row(s)")

    # ----------------------------------------------------------------- reads

    def list_metrics(self) -> list[str]:
        """
        Name every metric with data in any tier.

        Returns:
            Metric names, sorted.
        """
        # The newest-reading table plus what the hourly tier saw this past
        # week: a metric silent for longer is history nobody is asking for, and
        # scanning every tier's rows for its name would cost a table scan on
        # every page that lists them.
        floor = int(self._clock()) - TEN_MINUTES.retention  # type: ignore[operator]
        rows = self._get_connection().execute(
            "SELECT metric FROM latest UNION SELECT metric FROM samples WHERE ts >= ? "
            "UNION SELECT DISTINCT metric FROM consolidated WHERE resolution = ? AND ts >= ? "
            "ORDER BY metric",
            (floor, HOUR.resolution, floor),
        )
        return [str(row[0]) for row in rows]

    def latest_values(
        self, *, max_age_s: float | None = None, now: float | None = None
    ) -> dict[str, float]:
        """
        Read the newest value of every metric.

        This is what the console's live feed shows when the collector runs in
        another process: a primary-key table scan, not a query over samples.

        Args:
            max_age_s: Skip metrics whose newest value is older than this.
            now: The current time; defaults to the injected clock.

        Returns:
            Metric name to its newest value.
        """
        moment = float(self._clock()) if now is None else float(now)
        floor = 0 if max_age_s is None else int(moment - max_age_s)
        rows = self._get_connection().execute(
            "SELECT metric, value FROM latest WHERE ts >= ?", (floor,)
        )
        return {str(row[0]): float(row[1]) for row in rows}

    def last_sample_at(self, metrics: Sequence[str]) -> int | None:
        """
        Say when the newest of these metrics was recorded.

        Args:
            metrics: Metric names.

        Returns:
            Epoch seconds, or None when none has data in the newest-reading
            table (nothing in the last day).
        """
        if not metrics:
            return None
        sql = (
            "SELECT MAX(ts) FROM latest WHERE metric IN "  # noqa: S608 - "?" markers only
            f"({_marks(len(metrics))})"
        )
        row = self._get_connection().execute(sql, tuple(metrics)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def first_sample_at(self, metrics: Sequence[str]) -> int | None:
        """
        Say when the oldest of these metrics was recorded, in any tier.

        A tier's oldest row is the *start of its bucket*, which precedes the
        first sample by up to a bucket: an hour-old series would read as starting
        on the hour. So a coarser tier only moves the answer back when it holds
        a whole bucket of data before what the finer tiers already show, which
        alignment cannot explain.

        Args:
            metrics: Metric names.

        Returns:
            Epoch seconds, or None when none has any data.
        """
        conn = self._get_connection()
        oldest: int | None = None
        for tier in TIERS:
            firsts = [
                ts for ts in (self._first_in(conn, m, tier) for m in metrics) if ts is not None
            ]
            if not firsts:
                continue
            first = min(firsts)
            if oldest is None or first + tier.step <= oldest:
                oldest = first
        return oldest

    @staticmethod
    def _first_in(conn: sqlite3.Connection, metric: str, tier: Tier) -> int | None:
        """
        Read the oldest bucket of one metric in one tier.

        Args:
            conn: This thread's connection.
            metric: Metric name.
            tier: The tier to look in.

        Returns:
            Epoch seconds, or None when the tier has nothing for the metric.
        """
        if tier is RAW:
            row = conn.execute("SELECT MIN(ts) FROM samples WHERE metric = ?", (metric,)).fetchone()
        else:
            row = conn.execute(
                "SELECT MIN(ts) FROM consolidated WHERE metric = ? AND resolution = ?",
                (metric, tier.resolution),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def query_range(
        self,
        metrics: Sequence[str],
        *,
        start: int,
        end: int,
        step: int | None = None,
        max_points: int = MAX_RANGE_POINTS,
    ) -> RangeResult:
        """
        Read several metrics over ``[start, end]`` as a regular grid.

        The tier is the finest whose retention reaches back to ``start`` and
        whose buckets give at most ``max_points`` cells; a window wider than
        that widens the cell to a multiple of the tier's step. A tier that does
        not actually hold data back to ``start`` yields to the next coarser one
        that does, so an upgraded store answers a week from its hourly history
        until the ten-minute tier has filled.

        Args:
            metrics: Metric names, at most :data:`MAX_RANGE_SERIES`.
            start: Start of the domain, epoch seconds.
            end: End of the domain; may be in the future (those cells are
                gaps).
            step: Seconds per cell, a multiple of a tier's step; chosen from
                the window when None.
            max_points: Most cells per series.

        Returns:
            The grid, the tier read, and one series per metric, with None for
            every cell where nothing was recorded.

        Raises:
            ValidationError: When the range, the step or the metric list is not
                acceptable.
        """
        names = list(dict.fromkeys(metrics))
        if not names:
            raise ValidationError("A range query needs at least one metric")
        if len(names) > MAX_RANGE_SERIES:
            raise ValidationError(
                f"A range query may ask for at most {MAX_RANGE_SERIES} metrics, got {len(names)}"
            )
        start, end = int(start), int(end)
        if end <= start:
            raise ValidationError(f"The range must end after it starts (from {start} to {end})")
        if end - start > MAX_RANGE_SECONDS:
            raise ValidationError(
                f"The range is {end - start} seconds; the widest that can be read is "
                f"{MAX_RANGE_SECONDS}"
            )
        if max_points <= 0:
            raise ValidationError(f"max_points must be positive, got {max_points}")
        if step is not None and step <= 0:
            raise ValidationError(f"step must be positive, got {step}")

        now = int(self._clock())
        conn = self._get_connection()
        tier, cell = self._plan_read(conn, names, start, end, now, step, max_points)

        first_cell = -(-start // cell) * cell
        last_cell = end // cell * cell
        grid = list(range(first_cell, last_cell + 1, cell))

        cells = self._read_cells(conn, names, tier, cell, first_cell, last_cell, now)
        series = []
        for name in names:
            found = cells.get(name, {})
            points = [(t, *found.get(t, (None, None))) for t in grid]
            series.append(RangeSeries(name, points, self._ceiling_of(conn, name)))

        return RangeResult(
            start=start,
            end=end,
            step=cell,
            resolution=tier.name,
            first_sample_at=self.first_sample_at(names),
            last_sample_at=self.last_sample_at(names),
            series=series,
        )

    def _plan_read(
        self,
        conn: sqlite3.Connection,
        names: Sequence[str],
        start: int,
        end: int,
        now: int,
        step: int | None,
        max_points: int,
    ) -> tuple[Tier, int]:
        """
        Choose the tier to read and the width of a cell.

        Args:
            conn: This thread's connection.
            names: The metrics asked for.
            start: Start of the domain.
            end: End of the domain.
            now: The current time.
            step: The step the caller asked for, or None.
            max_points: Most cells per series.

        Returns:
            The tier, and the cell width in seconds (a multiple of its step).

        Raises:
            ValidationError: When an explicit step is not a multiple of any
                tier's step, or asks for more cells than allowed.
        """
        span = end - start
        eligible = [tier for tier in TIERS if self._reaches_back(tier, start, now)]
        if step is not None:
            divisors = [tier for tier in eligible if step % tier.step == 0]
            if not divisors:
                raise ValidationError(
                    f"step {step} is not a multiple of any tier's step "
                    f"({', '.join(str(t.step) for t in eligible)}) that reaches this far back"
                )
            if span // step + 1 > max_points:
                raise ValidationError(
                    f"step {step} over {span} seconds is more than {max_points} points"
                )
            # The coarsest tier that divides the step: fewest rows to read.
            preferred = divisors[-1]
            return self._prefer_covering(conn, names, [preferred], start, preferred), step

        nominal = next((t for t in eligible if span / t.step <= max_points), eligible[-1])
        candidates = [t for t in eligible if t.step >= nominal.step]
        tier = self._prefer_covering(conn, names, candidates, start, nominal)
        cell = tier.step * max(1, -(-span // (tier.step * max_points)))
        return tier, cell

    def _reaches_back(self, tier: Tier, start: int, now: int) -> bool:
        """
        Say whether a tier's retention reaches back to ``start``.

        Args:
            tier: The tier.
            start: Start of the domain.
            now: The current time.

        Returns:
            True when it does; the hourly tier always qualifies, being the
            coarsest there is.
        """
        if tier is HOUR:
            return True
        return start >= now - (tier.retention or 0)

    def _prefer_covering(
        self,
        conn: sqlite3.Connection,
        names: Sequence[str],
        candidates: Sequence[Tier],
        start: int,
        nominal: Tier,
    ) -> Tier:
        """
        Pick, among candidate tiers, the first that holds data back to ``start``.

        Args:
            conn: This thread's connection.
            names: The metrics asked for.
            candidates: Tiers to consider, finest first.
            start: Start of the domain.
            nominal: The tier the window would use with full history.

        Returns:
            The first candidate whose oldest data is no later than one nominal
            bucket past ``start``; failing that, the one whose data reaches
            furthest back (the finest on a tie); with no data anywhere,
            ``nominal``.
        """
        best: Tier | None = None
        best_first: int | None = None
        for tier in candidates:
            first = self._first_covered(conn, names, tier)
            if first is None:
                continue
            # The tolerance is the nominal tier's step, the same for every
            # candidate: a coarse tier's own step would be a whole bucket of
            # slack, and every tier would then "cover" any window. A bucket
            # covers ``start`` only once it ends by then: its first row is the
            # start of the bucket, not of the data, and an hourly bucket at
            # 23:00 holding readings from 23:58 does not cover a window that
            # starts at 23:26 (the last hour of a young history read as one
            # hourly cell instead of its five-second readings).
            if first + tier.step <= start + nominal.step:
                return tier
            # A coarser tier's first row is the start of its bucket, which
            # precedes the data by up to a bucket; it reaches further back only
            # when it holds a whole bucket more than the tier before it.
            if best_first is None or first + tier.step <= best_first:
                best, best_first = tier, first
        return best or nominal

    def _first_covered(
        self, conn: sqlite3.Connection, names: Sequence[str], tier: Tier
    ) -> int | None:
        """
        Find the oldest bucket a read of this tier would return.

        Buckets the tier has not consolidated yet come from the raw tier, so
        raw data counts towards every tier.

        Args:
            conn: This thread's connection.
            names: The metrics asked for.
            tier: The tier.

        Returns:
            Epoch seconds, or None when neither the tier nor raw has data.
        """
        oldest: int | None = None
        for name in names:
            for source in {tier, RAW}:
                ts = self._first_in(conn, name, source)
                if ts is not None and (oldest is None or ts < oldest):
                    oldest = ts
        return oldest

    def _read_cells(
        self,
        conn: sqlite3.Connection,
        names: Sequence[str],
        tier: Tier,
        cell: int,
        first_cell: int,
        last_cell: int,
        now: int,
    ) -> dict[str, dict[int, tuple[float, float]]]:
        """
        Aggregate the tier's rows into grid cells, then fill from raw.

        Args:
            conn: This thread's connection.
            names: The metrics asked for.
            tier: The tier read.
            cell: Seconds per cell.
            first_cell: Start of the first cell.
            last_cell: Start of the last cell.
            now: The current time.

        Returns:
            Metric name to cell start to ``(mean, maximum)``. A cell in the tier
            wins; raw fills a cell the tier does not have (a bucket not
            consolidated yet, the newest ones).
        """
        marks = _marks(len(names))
        found: dict[str, dict[int, tuple[float, float]]] = {}
        upper = last_cell + cell

        if tier is not RAW:
            tier_sql = (
                "SELECT metric, (ts / ?) * ?, AVG(value), MAX(COALESCE(max_value, value)) "  # noqa: S608 - "?" markers only
                "FROM consolidated "
                f"WHERE resolution = ? AND metric IN ({marks}) "
                "AND ts >= ? AND ts < ? GROUP BY metric, ts / ?"
            )
            rows = conn.execute(
                tier_sql,
                (cell, cell, tier.resolution, *names, first_cell, upper, cell),
            )
            for metric, ts, mean, peak in rows:
                found.setdefault(str(metric), {})[int(ts)] = (float(mean), float(peak))

        # Raw is read only where it can matter: the part of the window inside
        # its retention. For the raw tier itself that is all of it.
        raw_floor = max(first_cell, -(-(now - (RAW.retention or 0)) // cell) * cell)
        if raw_floor < upper:
            raw_sql = (
                "SELECT metric, (ts / ?) * ?, AVG(value), MAX(value) FROM samples "  # noqa: S608 - "?" markers only
                f"WHERE metric IN ({marks}) "
                "AND ts >= ? AND ts < ? GROUP BY metric, ts / ?"
            )
            rows = conn.execute(raw_sql, (cell, cell, *names, raw_floor, upper, cell))
            for metric, ts, mean, peak in rows:
                per_metric = found.setdefault(str(metric), {})
                per_metric.setdefault(int(ts), (float(mean), float(peak)))
        return found

    def _ceiling_of(self, conn: sqlite3.Connection, metric: str) -> float | None:
        """
        Read the ceiling a metric is drawn against.

        Args:
            conn: This thread's connection.
            metric: Metric name.

        Returns:
            The newest value of its paired ``*.total_bytes`` metric, or None
            for a metric without one or without a reading.
        """
        pair = CEILINGS.get(metric)
        if pair is None:
            return None
        row = conn.execute("SELECT value FROM latest WHERE metric = ?", (pair,)).fetchone()
        if row is not None:
            return float(row[0])
        # Older than a day, so no longer in the newest-reading table: the
        # hourly tier still has it.
        row = conn.execute(
            "SELECT value FROM consolidated WHERE metric = ? AND resolution = ? "
            "ORDER BY ts DESC LIMIT 1",
            (pair, HOUR.resolution),
        ).fetchone()
        return float(row[0]) if row is not None else None

    def query(
        self, metric: str, *, window_s: int, max_points: int = DEFAULT_MAX_POINTS
    ) -> list[tuple[int, float]]:
        """
        Read one metric over the last ``window_s`` seconds, oldest first.

        The single-metric read the console used before ``query_range``: the
        means of the non-empty cells, without the gaps. Kept until the console
        reads ranges (Noust 3.2 removes it).

        Args:
            metric: Metric name.
            window_s: Width of the window ending now, in seconds.
            max_points: Accepted for compatibility and validated; the tier is
                chosen by the window (at most :data:`MAX_RANGE_POINTS` points).

        Returns:
            ``(ts, mean)`` pairs sorted by timestamp.

        Raises:
            ValueError: If ``window_s`` or ``max_points`` is not positive.
        """
        if window_s <= 0:
            raise ValueError(f"window_s must be positive, got {window_s}")
        if max_points <= 0:
            raise ValueError(f"max_points must be positive, got {max_points}")
        end = int(self._clock())
        result = self.query_range([metric], start=end - int(window_s), end=end)
        return [(ts, mean) for ts, mean, _peak in result.series[0].points if mean is not None]

    # ------------------------------------------------------ collector lease

    def acquire_collector(
        self,
        owner: str,
        *,
        kind: str,
        interval_s: float,
        pid: int | None = None,
        now: float | None = None,
    ) -> bool:
        """
        Take the collector lease if nobody else holds it, or refresh it.

        The lease is taken when there is none, when its holder stopped
        heartbeating, when it is already ours, or when ours outranks the
        holder's (the daemon takes it from the console).

        Args:
            owner: A unique id for this collector instance.
            kind: ``daemon`` or ``console``.
            interval_s: Seconds between this collector's ticks.
            pid: The process id; defaults to this process's.
            now: The current time; defaults to the injected clock.

        Returns:
            True when this collector now holds the lease.

        Raises:
            ValidationError: For a ``kind`` that is neither.
        """
        if kind not in _KIND_PRIORITY:
            raise ValidationError(f"Unknown collector kind {kind!r}")
        moment = int(self._clock() if now is None else now)
        with self._immediate() as conn:
            row = conn.execute("SELECT * FROM collector WHERE id = 1").fetchone()
            if row is not None and row["owner"] != owner:
                held = CollectorLease(
                    owner=row["owner"],
                    kind=row["kind"],
                    pid=row["pid"],
                    started_at=row["started_at"],
                    heartbeat_at=row["heartbeat_at"],
                    interval_s=row["interval_s"],
                )
                outranks = _KIND_PRIORITY[kind] > _KIND_PRIORITY.get(held.kind, 0)
                if held.is_live(moment) and not outranks:
                    return False
            conn.execute(
                """
                INSERT INTO collector
                    (id, owner, kind, pid, started_at, heartbeat_at, interval_s, ticks)
                VALUES (1, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT (id) DO UPDATE SET
                    owner = excluded.owner, kind = excluded.kind, pid = excluded.pid,
                    started_at = CASE WHEN collector.owner = excluded.owner
                                      THEN collector.started_at ELSE excluded.started_at END,
                    heartbeat_at = excluded.heartbeat_at, interval_s = excluded.interval_s,
                    ticks = CASE WHEN collector.owner = excluded.owner
                                 THEN collector.ticks ELSE 0 END,
                    last_error = CASE WHEN collector.owner = excluded.owner
                                      THEN collector.last_error ELSE NULL END,
                    last_error_at = CASE WHEN collector.owner = excluded.owner
                                         THEN collector.last_error_at ELSE NULL END
                """,
                (owner, kind, os.getpid() if pid is None else pid, moment, moment, interval_s),
            )
        return True

    def heartbeat_collector(
        self,
        owner: str,
        *,
        error: str | None = None,
        now: float | None = None,
    ) -> bool:
        """
        Record that the collector just ticked.

        Args:
            owner: The collector's id.
            error: An operational failure of this tick, verbatim, to keep for
                whoever asks why history has holes.
            now: The current time; defaults to the injected clock.

        Returns:
            False when the lease is no longer this collector's (another one
            took it): the caller must stop sampling.
        """
        moment = int(self._clock() if now is None else now)
        conn = self._get_connection()
        with conn:
            if error is None:
                changed = conn.execute(
                    "UPDATE collector SET heartbeat_at = ?, ticks = ticks + 1 "
                    "WHERE id = 1 AND owner = ?",
                    (moment, owner),
                ).rowcount
            else:
                changed = conn.execute(
                    "UPDATE collector SET heartbeat_at = ?, ticks = ticks + 1, "
                    "last_error = ?, last_error_at = ? WHERE id = 1 AND owner = ?",
                    (moment, error[:2000], moment, owner),
                ).rowcount
        return changed == 1

    def release_collector(self, owner: str) -> None:
        """
        Give the lease up, if it is this collector's.

        Args:
            owner: The collector's id.
        """
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM collector WHERE id = 1 AND owner = ?", (owner,))

    def collector_lease(self) -> CollectorLease | None:
        """
        Read who holds the lease, live or not.

        Returns:
            The lease, or None when no collector has ever run against this
            database (or the last one released it).
        """
        row = self._get_connection().execute("SELECT * FROM collector WHERE id = 1").fetchone()
        if row is None:
            return None
        return CollectorLease(
            owner=row["owner"],
            kind=row["kind"],
            pid=row["pid"],
            started_at=row["started_at"],
            heartbeat_at=row["heartbeat_at"],
            interval_s=row["interval_s"],
            ticks=row["ticks"],
            last_error=row["last_error"],
            last_error_at=row["last_error_at"],
        )

    # ------------------------------------------------------------ log cursors

    def read_log_cursor(self, path: str) -> tuple[int, int] | None:
        """
        Read where reading an access log stopped.

        Args:
            path: The log file.

        Returns:
            ``(inode, position)``, or None for a log never read.
        """
        row = (
            self._get_connection()
            .execute("SELECT inode, position FROM log_cursors WHERE path = ?", (path,))
            .fetchone()
        )
        return (int(row[0]), int(row[1])) if row is not None else None

    def write_log_cursor(self, path: str, inode: int, position: int) -> None:
        """
        Remember where reading an access log stopped.

        Args:
            path: The log file.
            inode: Its inode, so a rotated file is recognised.
            position: The byte offset to resume from.
        """
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO log_cursors (path, inode, position, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (path) DO UPDATE SET
                    inode = excluded.inode, position = excluded.position,
                    updated_at = excluded.updated_at
                """,
                (path, inode, position, int(self._clock())),
            )

    def prune_log_cursors(self, keep: Iterable[str]) -> None:
        """
        Forget the cursors of logs that are no longer read.

        Args:
            keep: The paths still in use.
        """
        wanted = set(keep)
        conn = self._get_connection()
        with conn:
            rows = conn.execute("SELECT path FROM log_cursors").fetchall()
            for (path,) in rows:
                if path not in wanted:
                    conn.execute("DELETE FROM log_cursors WHERE path = ?", (path,))
