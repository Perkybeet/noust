# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the RRD-style metrics store.

Every test drives the store through an injected clock and an injected database
path: nothing here depends on the wall clock or writes outside the test's own
directory. Where a test needs to see the physical tables (that a roll-up really
wrote a maximum, that retention really deleted the rows) it opens the SQLite
file directly instead of trusting the API under test to report on itself.

What is defended:

- **A range read returns the window that was asked for**, as a regular grid over
  ``[start, end]`` with ``None`` where nothing was recorded. The chart draws the
  window, not the span of the data (the "selector changes nothing" defect).
- **Every window falls in one tier and says which.** The label is the tier
  actually read, never the one the window's width would suggest.
- **Both the mean and the maximum survive every tier**, so a short spike is
  still visible in the month view.
- **A store upgraded from the three-tier design keeps its history** and answers
  a week from its hourly tier until the ten-minute one has filled.
- **The collector lease admits one collector**, and the daemon outranks the
  console.
"""

from __future__ import annotations

import sqlite3
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.monitor.timeseries import (
    DEFAULT_RETENTION_DAYS,
    HOUR,
    MAX_RANGE_POINTS,
    MINUTE,
    RAW,
    SCHEMA_VERSION,
    TEN_MINUTES,
    MetricsStore,
    lease_timeout,
    resolve_retention_days,
)

#: A fixed "now", divisible by 3600 so buckets land on round timestamps the
#: assertions can spell out.
NOW = 1_700_002_800

HOURS = 3_600
DAY = 86_400


class FrozenClock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = NOW) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FrozenClock:
    """Provide a clock frozen at :data:`NOW`."""
    return FrozenClock()


@pytest.fixture
def store(tmp_path: Path, clock: FrozenClock) -> MetricsStore:
    """Provide a store on a throwaway database with the frozen clock."""
    return MetricsStore(tmp_path / "metrics.db", clock=clock)


def rows(store: MetricsStore, sql: str, *params: Any) -> list[Any]:
    """Read straight from the file, bypassing the store."""
    conn = sqlite3.connect(str(store.db_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def fill(
    store: MetricsStore,
    metric: str,
    *,
    start: int,
    end: int,
    every: int,
    value: Any = 1.0,
) -> None:
    """Record ``metric`` every ``every`` seconds from ``start`` to ``end``."""
    for ts in range(start, end, every):
        store.record(metric, value(ts) if callable(value) else value, ts=ts)


# ------------------------------------------------------------ writes and reads


def test_record_and_query_roundtrip_in_time_order(store: MetricsStore) -> None:
    """Samples come back sorted by timestamp regardless of insertion order."""
    store.record("cpu.percent", 3.0, ts=NOW - 20)
    store.record("cpu.percent", 1.0, ts=NOW - 60)
    store.record("cpu.percent", 2.0, ts=NOW - 40)

    points = store.query("cpu.percent", window_s=HOURS)

    assert [value for _ts, value in points] == [1.0, 2.0, 3.0]
    assert points == sorted(points)


def test_recording_the_same_second_twice_keeps_the_last_value(store: MetricsStore) -> None:
    """A collector landing twice on one timestamp is reporting a gauge: newest wins."""
    store.record("cpu.percent", 1.0, ts=NOW - 5)
    store.record("cpu.percent", 9.0, ts=NOW - 5)

    assert store.query("cpu.percent", window_s=60) == [(NOW - 5, 9.0)]


def test_record_many_writes_one_tick_at_one_timestamp(store: MetricsStore) -> None:
    """A tick is one transaction: every metric of it shares the stamp."""
    store.record_many([("cpu.percent", 1.0), ("mem.used_bytes", 2.0)], ts=NOW - 5)

    assert rows(store, "SELECT metric, ts FROM samples ORDER BY metric") == [
        ("cpu.percent", NOW - 5),
        ("mem.used_bytes", NOW - 5),
    ]


def test_the_newest_reading_of_a_metric_is_one_primary_key_read(store: MetricsStore) -> None:
    """The live feed never scans samples: a late write cannot move 'latest' backwards."""
    store.record("cpu.percent", 5.0, ts=NOW - 10)
    store.record("cpu.percent", 1.0, ts=NOW - 500)

    assert store.latest_values() == {"cpu.percent": 5.0}
    assert store.latest_values(max_age_s=5) == {}
    assert store.latest_values(max_age_s=60) == {"cpu.percent": 5.0}
    assert store.last_sample_at(["cpu.percent", "other"]) == NOW - 10


# ------------------------------------------------------------------ the range


def test_a_range_returns_the_requested_domain_as_a_regular_grid(store: MetricsStore) -> None:
    """The response names [start, end] and a cell for every step in it."""
    fill(store, "cpu.percent", start=NOW - 600, end=NOW, every=5, value=2.0)

    result = store.query_range(["cpu.percent"], start=NOW - 3_600, end=NOW)

    assert (result.start, result.end) == (NOW - 3_600, NOW)
    assert result.resolution == "raw"
    assert result.step == RAW.step
    grid = [ts for ts, _mean, _peak in result.series[0].points]
    assert grid[0] == NOW - 3_600
    assert grid[-1] == NOW
    assert all(b - a == 5 for a, b in pairwise(grid))
    assert len(grid) == 3_600 // 5 + 1


def test_a_window_wider_than_the_data_shows_the_gap_not_a_short_axis(
    store: MetricsStore,
) -> None:
    """One hour of samples in a 24 h window: the day is there, the rest is null."""
    fill(store, "cpu.percent", start=NOW - 3_600, end=NOW, every=5, value=4.0)
    store.consolidate()

    result = store.query_range(["cpu.percent"], start=NOW - DAY, end=NOW)

    points = result.series[0].points
    assert result.resolution == "1m"
    assert points[0][0] == NOW - DAY
    assert points[-1][0] == NOW
    assert len(points) == 1_441
    filled = [ts for ts, mean, _peak in points if mean is not None]
    empty = [ts for ts, mean, _peak in points if mean is None]
    assert min(filled) >= NOW - 3_600
    assert len(empty) > 1_300
    assert result.first_sample_at == NOW - 3_600


@pytest.mark.parametrize(
    ("window_s", "resolution", "step", "points"),
    [
        (HOURS, "raw", 5, 721),
        (DAY, "1m", 60, 1_441),
        (7 * DAY, "10m", 600, 1_009),
        (30 * DAY, "1h", 3_600, 721),
    ],
)
def test_each_selector_window_falls_in_exactly_one_native_tier(
    store: MetricsStore, window_s: int, resolution: str, step: int, points: int
) -> None:
    """1 h, 24 h, 7 d and 30 d each read one tier at its own step: no bucketing."""
    store.record("cpu.percent", 1.0, ts=NOW - 10)

    result = store.query_range(["cpu.percent"], start=NOW - window_s, end=NOW)

    assert result.resolution == resolution
    assert result.step == step
    assert len(result.series[0].points) == points


def test_a_gap_in_the_middle_is_null_and_never_bridged(store: MetricsStore) -> None:
    """A collector that was down for 20 hours leaves 20 hours of nothing."""
    fill(store, "cpu.percent", start=NOW - DAY, end=NOW - 21 * HOURS, every=60, value=1.0)
    fill(store, "cpu.percent", start=NOW - HOURS, end=NOW, every=60, value=2.0)
    store.consolidate()

    result = store.query_range(["cpu.percent"], start=NOW - DAY, end=NOW)

    by_ts = {ts: mean for ts, mean, _peak in result.series[0].points}
    assert by_ts[NOW - DAY + 60] == 1.0
    assert by_ts[NOW - 10 * HOURS] is None
    assert by_ts[NOW - 60] == 2.0


def test_the_maximum_survives_every_tier(store: MetricsStore) -> None:
    """A five minute spike is invisible in an hourly mean; the maximum keeps it."""
    fill(
        store,
        "cpu.percent",
        start=NOW - 4 * HOURS,
        end=NOW - 2 * HOURS,
        every=5,
        value=lambda ts: 95.0 if ts == NOW - 3 * HOURS + 300 else 10.0,
    )
    store.consolidate()

    minute = store.query_range(["cpu.percent"], start=NOW - 6 * HOURS, end=NOW)
    ten = store.query_range(["cpu.percent"], start=NOW - 3 * DAY, end=NOW)
    hour = store.query_range(["cpu.percent"], start=NOW - 30 * DAY, end=NOW)

    for result in (minute, ten, hour):
        peaks = [peak for _ts, _mean, peak in result.series[0].points if peak is not None]
        assert max(peaks) == 95.0, result.resolution
    hourly_means = [mean for _ts, mean, _peak in hour.series[0].points if mean is not None]
    assert max(hourly_means) < 20.0


def test_a_roll_up_writes_the_maximum_next_to_the_mean(store: MetricsStore) -> None:
    """The physical rows: value is the mean, max_value the peak."""
    fill(
        store,
        "cpu.percent",
        start=NOW - 3 * HOURS,
        end=NOW - 3 * HOURS + 60,
        every=5,
        value=lambda ts: float(ts - (NOW - 3 * HOURS)),
    )
    store.consolidate()

    (row,) = rows(
        store,
        "SELECT ts, value, max_value FROM consolidated WHERE resolution = ?",
        MINUTE.resolution,
    )
    assert row[0] == NOW - 3 * HOURS
    assert row[1] == pytest.approx(sum(range(0, 60, 5)) / 12)
    assert row[2] == 55.0


def test_the_buckets_not_consolidated_yet_come_from_raw(store: MetricsStore) -> None:
    """The right edge of a day-wide chart is never blank: raw fills the newest minutes."""
    fill(store, "cpu.percent", start=NOW - 600, end=NOW + 1, every=5, value=7.0)

    result = store.query_range(["cpu.percent"], start=NOW - DAY, end=NOW)

    assert result.resolution == "1m"
    tail = [mean for _ts, mean, _peak in result.series[0].points[-5:]]
    assert tail == [7.0] * 5


def test_a_wider_window_than_a_tier_can_carry_widens_the_cell(store: MetricsStore) -> None:
    """A year is 8760 hourly buckets: the cell becomes a multiple of an hour."""
    store.record("cpu.percent", 1.0, ts=NOW - 10)

    result = store.query_range(["cpu.percent"], start=NOW - 365 * DAY, end=NOW)

    assert result.resolution == "1h"
    assert result.step % HOUR.step == 0
    assert result.step > HOUR.step
    assert len(result.series[0].points) <= MAX_RANGE_POINTS


def test_several_metrics_share_one_grid(store: MetricsStore) -> None:
    """The batch read gives every series the same timestamps, so charts can align."""
    store.record("cpu.percent", 1.0, ts=NOW - 10)
    store.record("mem.used_bytes", 2.0, ts=NOW - 10)

    result = store.query_range(
        ["cpu.percent", "mem.used_bytes", "nothing"], start=NOW - 60, end=NOW
    )

    grids = [[ts for ts, _m, _p in s.points] for s in result.series]
    assert grids[0] == grids[1] == grids[2]
    assert [s.metric for s in result.series] == ["cpu.percent", "mem.used_bytes", "nothing"]
    assert all(mean is None for _ts, mean, _peak in result.series[2].points)


def test_a_memory_series_carries_its_ceiling(store: MetricsStore) -> None:
    """Total memory is a property of the used series, not a series of its own."""
    store.record("mem.used_bytes", 5.0, ts=NOW - 10)
    store.record("mem.total_bytes", 16.0, ts=NOW - 10)

    result = store.query_range(["mem.used_bytes", "cpu.percent"], start=NOW - 60, end=NOW)

    assert result.series[0].ceiling == 16.0
    assert result.series[1].ceiling is None


def test_the_window_after_now_is_gaps(store: MetricsStore) -> None:
    """A domain that ends in the future is honoured, the future being empty."""
    store.record("cpu.percent", 1.0, ts=NOW - 10)

    result = store.query_range(["cpu.percent"], start=NOW - 60, end=NOW + 60)

    assert result.series[0].points[-1][0] == NOW + 60
    assert result.series[0].points[-1][1] is None


def test_a_step_that_is_not_a_tier_multiple_is_refused(store: MetricsStore) -> None:
    """The guard is in the store, not in whichever endpoint calls it."""
    with pytest.raises(ValidationError):
        store.query_range(["cpu.percent"], start=NOW - HOURS, end=NOW, step=7)


def test_an_explicit_step_uses_the_coarsest_tier_that_divides_it(store: MetricsStore) -> None:
    """Zooming to five-minute cells reads the one-minute tier, not raw."""
    store.record("cpu.percent", 1.0, ts=NOW - 10)

    result = store.query_range(["cpu.percent"], start=NOW - HOURS, end=NOW, step=300)

    assert result.step == 300
    assert result.resolution == "1m"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": NOW, "end": NOW},
        {"start": NOW, "end": NOW - 1},
        {"start": NOW - 10 * 3_650 * DAY, "end": NOW},
        {"start": NOW - HOURS, "end": NOW, "step": 0},
        {"start": NOW - HOURS, "end": NOW, "step": 5, "max_points": 100},
    ],
)
def test_impossible_ranges_are_refused(store: MetricsStore, kwargs: dict[str, Any]) -> None:
    """From after to, an absurd width, a step that would return too many cells."""
    with pytest.raises(ValidationError):
        store.query_range(["cpu.percent"], **kwargs)


def test_no_metrics_or_too_many_are_refused(store: MetricsStore) -> None:
    """A batch is bounded: the API cannot be asked for the whole database."""
    with pytest.raises(ValidationError):
        store.query_range([], start=NOW - 60, end=NOW)
    with pytest.raises(ValidationError):
        store.query_range([f"m{i}" for i in range(41)], start=NOW - 60, end=NOW)


def test_query_rejects_a_nonpositive_window(store: MetricsStore) -> None:
    """The legacy read keeps its contract."""
    with pytest.raises(ValueError):
        store.query("cpu.percent", window_s=0)
    with pytest.raises(ValueError):
        store.query("cpu.percent", window_s=60, max_points=0)


# --------------------------------------------------------------- consolidation


def test_consolidate_twice_changes_nothing(store: MetricsStore) -> None:
    """Only complete buckets are aggregated, so the second run is a no-op."""
    fill(store, "cpu.percent", start=NOW - 5 * HOURS, end=NOW, every=5, value=2.0)
    store.consolidate()
    first = rows(store, "SELECT * FROM consolidated ORDER BY resolution, ts")

    store.consolidate()

    assert rows(store, "SELECT * FROM consolidated ORDER BY resolution, ts") == first
    assert first, "nothing was consolidated"


def test_each_tier_keeps_its_whole_retention_on_its_own(store: MetricsStore) -> None:
    """Rolling up does not delete the data that is still inside the finer tier's window."""
    fill(store, "cpu.percent", start=NOW - 3 * HOURS, end=NOW, every=60, value=1.0)
    store.consolidate()

    minute_rows = rows(
        store, "SELECT COUNT(*) FROM consolidated WHERE resolution = ?", MINUTE.resolution
    )
    ten_rows = rows(
        store, "SELECT COUNT(*) FROM consolidated WHERE resolution = ?", TEN_MINUTES.resolution
    )
    assert minute_rows[0][0] == 180
    assert ten_rows[0][0] == 18


def test_raw_is_dropped_after_two_hours_but_only_once_rolled_up(store: MetricsStore) -> None:
    """The raw tier keeps two hours; what it drops has already become minute rows."""
    fill(store, "cpu.percent", start=NOW - 5 * HOURS, end=NOW, every=5, value=1.0)

    store.consolidate()

    oldest_raw = rows(store, "SELECT MIN(ts) FROM samples")[0][0]
    assert oldest_raw >= NOW - 2 * HOURS - 60
    oldest_minute = rows(
        store, "SELECT MIN(ts) FROM consolidated WHERE resolution = ?", MINUTE.resolution
    )[0][0]
    assert oldest_minute == NOW - 5 * HOURS


def test_consolidate_chains_through_every_tier_and_expires_after_the_retention(
    store: MetricsStore, clock: FrozenClock
) -> None:
    """A month-old sample reaches the hourly tier; one past 400 days is deleted."""
    store.record("cpu.percent", 5.0, ts=NOW - 30 * DAY)
    store.record("cpu.percent", 9.0, ts=NOW - 401 * DAY)

    store.consolidate()

    hourly = rows(
        store,
        "SELECT ts, value, max_value FROM consolidated WHERE resolution = ?",
        HOUR.resolution,
    )
    assert hourly == [((NOW - 30 * DAY) // HOURS * HOURS, 5.0, 5.0)]
    assert rows(store, "SELECT COUNT(*) FROM samples")[0][0] == 0


def test_the_hourly_retention_is_configurable(tmp_path: Path, clock: FrozenClock) -> None:
    """metrics.retention_days changes how long the hourly tier is kept."""
    short = MetricsStore(tmp_path / "short.db", clock=clock, retention_days=40)
    short.record("cpu.percent", 5.0, ts=NOW - 39 * DAY)
    short.record("cpu.percent", 6.0, ts=NOW - 41 * DAY)

    short.consolidate()

    kept = rows(short, "SELECT value FROM consolidated WHERE resolution = ?", HOUR.resolution)
    assert kept == [(5.0,)]


def test_the_retention_setting_is_clamped_and_read_forgivingly() -> None:
    """A typo cannot make the month view permanently short or keep ten years by accident."""
    assert resolve_retention_days(400) == 400
    assert resolve_retention_days("800") == 800
    assert resolve_retention_days(1) == 35
    assert resolve_retention_days(10**6) == 3_650
    assert resolve_retention_days("a lot") == DEFAULT_RETENTION_DAYS
    assert resolve_retention_days(None) == DEFAULT_RETENTION_DAYS


def test_a_lookback_only_reads_recent_source_rows(store: MetricsStore) -> None:
    """The collector's periodic run does not rescan two hours of samples."""
    fill(store, "cpu.percent", start=NOW - 90 * 60, end=NOW, every=5, value=1.0)

    store.consolidate(lookback=10 * 60)

    minute = rows(store, "SELECT MIN(ts) FROM consolidated WHERE resolution = ?", MINUTE.resolution)
    assert minute[0][0] >= NOW - 10 * 60 - 60


def test_metrics_stay_separate_through_consolidation(store: MetricsStore) -> None:
    """Two metrics in the same bucket produce two rows, never one blended mean."""
    fill(store, "a", start=NOW - 3 * HOURS, end=NOW - 3 * HOURS + 60, every=5, value=10.0)
    fill(store, "b", start=NOW - 3 * HOURS, end=NOW - 3 * HOURS + 60, every=5, value=20.0)

    store.consolidate()

    assert rows(
        store,
        "SELECT metric, value FROM consolidated WHERE resolution = ? ORDER BY metric",
        MINUTE.resolution,
    ) == [("a", 10.0), ("b", 20.0)]


def test_list_metrics_names_what_has_data(store: MetricsStore) -> None:
    """The catalogue reads the newest-reading table and the recent hourly tier."""
    store.record("b", 1.0, ts=NOW - 5)
    store.record("a", 1.0, ts=NOW - 5)

    assert store.list_metrics() == ["a", "b"]


# ------------------------------------------------------------------- upgrades


def test_a_v1_database_keeps_its_history_and_gains_the_maximum(
    tmp_path: Path, clock: FrozenClock
) -> None:
    """Rows the three-tier design wrote are still read; their maximum is their mean."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.executescript(
        """
        CREATE TABLE samples (metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL,
                              PRIMARY KEY (metric, ts)) WITHOUT ROWID;
        CREATE TABLE consolidated (metric TEXT NOT NULL, resolution INTEGER NOT NULL,
                                   ts INTEGER NOT NULL, value REAL NOT NULL,
                                   PRIMARY KEY (metric, resolution, ts)) WITHOUT ROWID;
        """
    )
    for hours_ago in range(1, 6):
        old.execute(
            "INSERT INTO consolidated VALUES ('cpu.percent', 3600, ?, ?)",
            (NOW - hours_ago * HOURS, 10.0 * hours_ago),
        )
    old.execute("INSERT INTO samples VALUES ('cpu.percent', ?, 4.0)", (NOW - 30,))
    old.commit()
    old.close()

    store = MetricsStore(path, clock=clock)

    result = store.query_range(["cpu.percent"], start=NOW - 30 * DAY, end=NOW)
    assert result.resolution == "1h"
    known = {ts: (mean, peak) for ts, mean, peak in result.series[0].points if mean is not None}
    assert known[NOW - 2 * HOURS] == (20.0, 20.0)
    assert store.latest_values() == {"cpu.percent": 4.0}
    assert rows(store, "PRAGMA user_version")[0][0] == SCHEMA_VERSION


def test_opening_the_store_twice_is_harmless(tmp_path: Path, clock: FrozenClock) -> None:
    """The daemon and the console open the same file at boot."""
    MetricsStore(tmp_path / "metrics.db", clock=clock).record("cpu.percent", 1.0, ts=NOW - 5)

    again = MetricsStore(tmp_path / "metrics.db", clock=clock)

    assert again.query("cpu.percent", window_s=60) == [(NOW - 5, 1.0)]


def test_a_week_is_read_from_hours_while_the_ten_minute_tier_is_young(
    store: MetricsStore,
) -> None:
    """After an upgrade the 7 d chart keeps its week, from the hourly tier, and says so."""
    conn = sqlite3.connect(str(store.db_path))
    for hours_ago in range(1, 7 * 24):
        conn.execute(
            "INSERT INTO consolidated (metric, resolution, ts, value, max_value) "
            "VALUES ('cpu.percent', 3600, ?, 5.0, NULL)",
            (NOW - hours_ago * HOURS,),
        )
    conn.execute(
        "INSERT INTO consolidated (metric, resolution, ts, value, max_value) "
        "VALUES ('cpu.percent', 600, ?, 6.0, 6.0)",
        (NOW - 3_000,),
    )
    conn.commit()
    conn.close()

    result = store.query_range(["cpu.percent"], start=NOW - 7 * DAY, end=NOW)

    assert result.resolution == "1h"
    assert result.step == 3_600
    filled = [mean for _ts, mean, _peak in result.series[0].points if mean is not None]
    assert len(filled) >= 160


def test_a_young_install_reads_the_finest_tier_it_has(store: MetricsStore) -> None:
    """With an hour of data everywhere equally short, the window keeps its nominal tier."""
    fill(store, "cpu.percent", start=NOW - HOURS, end=NOW, every=5, value=1.0)
    store.consolidate()

    assert store.query_range(["cpu.percent"], start=NOW - 7 * DAY, end=NOW).resolution == "10m"
    assert store.query_range(["cpu.percent"], start=NOW - 30 * DAY, end=NOW).resolution == "1h"


def test_an_hour_of_a_young_history_is_its_readings_not_one_hourly_cell(
    store: MetricsStore, clock: FrozenClock
) -> None:
    """Readings since 23:32, consolidated at 00:26 into the hourly bucket that began at 23:00.

    That bucket's first row (23:00) precedes the readings, and so seemed to cover the start of
    the last hour (23:26): the hour came back as one hourly cell instead of its readings.
    """
    clock.now = NOW + 26 * 60
    fill(store, "cpu.percent", start=NOW - 28 * 60, end=NOW + 26 * 60, every=5, value=1.0)
    store.consolidate()

    result = store.query_range(["cpu.percent"], start=NOW + 26 * 60 - HOURS, end=NOW + 26 * 60)

    assert result.resolution == "raw"
    assert result.step == 5


# ---------------------------------------------------------------------- lease


def test_only_one_collector_holds_the_lease(store: MetricsStore) -> None:
    """A second collector of the same kind is refused while the first is ticking."""
    assert store.acquire_collector("a", kind="daemon", interval_s=5)
    assert not store.acquire_collector("b", kind="daemon", interval_s=5)
    assert store.acquire_collector("a", kind="daemon", interval_s=5)


def test_a_silent_holder_loses_the_lease(store: MetricsStore, clock: FrozenClock) -> None:
    """After three missed ticks the holder is presumed dead and anybody may take over."""
    store.acquire_collector("a", kind="console", interval_s=5)
    clock.now += lease_timeout(5) + 1

    assert store.acquire_collector("b", kind="console", interval_s=5)
    lease = store.collector_lease()
    assert lease is not None and lease.owner == "b"


def test_the_daemon_takes_the_lease_from_the_console(store: MetricsStore) -> None:
    """The console samples only until a daemon exists; then it must notice and stop."""
    assert store.acquire_collector("web", kind="console", interval_s=5)

    assert store.acquire_collector("monitor", kind="daemon", interval_s=5)

    assert not store.heartbeat_collector("web")
    assert store.heartbeat_collector("monitor")
    assert not store.acquire_collector("web-again", kind="console", interval_s=5)


def test_a_heartbeat_keeps_the_last_error_for_whoever_asks(
    store: MetricsStore, clock: FrozenClock
) -> None:
    """Why history has holes: the newest failure, verbatim, on the lease row."""
    store.acquire_collector("a", kind="daemon", interval_s=5)
    clock.now += 5

    assert store.heartbeat_collector("a", error="database is locked")

    lease = store.collector_lease()
    assert lease is not None
    assert lease.ticks == 1
    assert lease.last_error == "database is locked"
    assert lease.heartbeat_at == int(clock.now)
    assert lease.is_live(clock.now)
    assert not lease.is_live(clock.now + lease_timeout(5) + 1)


def test_releasing_the_lease_frees_it(store: MetricsStore) -> None:
    """A clean stop hands the lease over at once."""
    store.acquire_collector("a", kind="daemon", interval_s=5)

    store.release_collector("b")
    assert store.collector_lease() is not None
    store.release_collector("a")

    assert store.collector_lease() is None
    assert store.acquire_collector("b", kind="console", interval_s=5)


def test_an_unknown_collector_kind_is_refused(store: MetricsStore) -> None:
    """The lease has two kinds; anything else is a bug worth failing on."""
    with pytest.raises(ValidationError):
        store.acquire_collector("a", kind="cron", interval_s=5)


# ---------------------------------------------------------------- log cursors


def test_a_log_cursor_survives_and_is_pruned(store: MetricsStore) -> None:
    """Where an access log was read to outlives the process that read it."""
    assert store.read_log_cursor("/var/log/nginx/a.access.log") is None

    store.write_log_cursor("/var/log/nginx/a.access.log", 42, 1000)
    store.write_log_cursor("/var/log/nginx/b.access.log", 43, 5)
    assert store.read_log_cursor("/var/log/nginx/a.access.log") == (42, 1000)

    store.write_log_cursor("/var/log/nginx/a.access.log", 42, 2000)
    store.prune_log_cursors(["/var/log/nginx/a.access.log"])

    assert store.read_log_cursor("/var/log/nginx/a.access.log") == (42, 2000)
    assert store.read_log_cursor("/var/log/nginx/b.access.log") is None
