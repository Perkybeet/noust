# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The chart's four windows over a collector that really ran, on a fake clock.

The "selector changes nothing" report came from three things at once: an axis
taken from the data instead of the window, no history because the collector only
ran with the console, and labels that named the window instead of the data. The
unit tests pin each piece; these drive the whole loop (the collector ticking on
its grid, the timer consolidating, the range read) through the situations the
investigation reproduced: a young history, a longer one, and one with a hole in
the middle.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.monitor import collector as collector_module
from noust.monitor.collector import MetricsCollector
from noust.monitor.sampler import MachineSampler
from noust.monitor.timeseries import MetricsStore

START = 1_700_000_000  # a multiple of 5, 60, 600 and 3600 is not needed: alignment is tested

HOUR = 3_600
DAY = 86_400


class World:
    """One clock for the store and the collector, moved by the test."""

    def __init__(self) -> None:
        self.wall = float(START)
        self.mono = 0.0

    def tick(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


class QuietPlanner:
    """No applications: this is about the machine's own series."""

    def build(self, apps: list[Any]) -> dict[str, Any]:
        return {}


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def store(tmp_path: Path, world: World) -> MetricsStore:
    return MetricsStore(tmp_path / "metrics.db", clock=lambda: world.wall)


@pytest.fixture
def collector(
    store: MetricsStore, world: World, monkeypatch: pytest.MonkeyPatch
) -> MetricsCollector:
    fake = SimpleNamespace(
        cpu_percent=lambda interval=None: 20.0,
        virtual_memory=lambda: SimpleNamespace(used=2_000, total=8_000, percent=25.0),
        swap_memory=lambda: SimpleNamespace(used=0, total=0, percent=0.0),
        disk_usage=lambda path: SimpleNamespace(used=10, total=100, percent=10.0),
        net_io_counters=lambda: None,
    )
    monkeypatch.setattr("noust.monitor.sampler.psutil", fake)
    return MetricsCollector(
        store,
        clock=lambda: world.mono,
        planner=QuietPlanner(),  # type: ignore[arg-type]
        apps_source=lambda: [],
        machine=MachineSampler(apps_root="/tmp", wall_clock=lambda: world.mono),
    )


def run(collector: MetricsCollector, world: World, seconds: int) -> None:
    """Let the collector tick every 5 seconds for a while."""
    for _ in range(seconds // 5):
        world.tick(5)
        collector.sample_once()


def query(store: MetricsStore, world: World, window: int) -> Any:
    end = int(world.wall)
    return store.query_range(["cpu.percent"], start=end - window, end=end)


def filled(result: Any) -> list[int]:
    return [ts for ts, mean, _peak in result.series[0].points if mean is not None]


def test_a_young_history_is_short_but_every_window_is_the_window_asked_for(
    collector: MetricsCollector, store: MetricsStore, world: World
) -> None:
    """Forty-five minutes of recording: four windows, four honest axes, one span of data."""
    run(collector, world, 45 * 60)

    results = {
        name: query(store, world, w)
        for name, w in (("1h", HOUR), ("24h", DAY), ("7d", 7 * DAY), ("30d", 30 * DAY))
    }

    assert {n: (r.resolution, r.step) for n, r in results.items()} == {
        "1h": ("raw", 5),
        "24h": ("1m", 60),
        "7d": ("10m", 600),
        "30d": ("1h", 3_600),
    }
    for name, window in (("1h", HOUR), ("24h", DAY), ("7d", 7 * DAY), ("30d", 30 * DAY)):
        result = results[name]
        assert result.end - result.start == window, "the axis is the window, not the data"
        assert result.series[0].points[0][0] <= result.start + result.step
        assert result.series[0].points[-1][0] >= result.end - result.step
        seen = filled(result)
        assert seen, f"{name} has no data at all"
        # Whatever the tier, the data spans the forty-five minutes and no more.
        assert max(seen) - min(seen) <= 45 * 60 + result.step
        assert result.first_sample_at is not None
        assert abs(result.first_sample_at - (START + 5)) <= 60
    week = results["7d"]
    assert len(filled(week)) < 10, "a week of axis, 45 minutes of data: the rest is gaps"
    assert len(week.series[0].points) in (1_008, 1_009), "a week is 1008 ten-minute cells"


def test_a_multi_hour_history_fills_the_finer_windows_and_says_so(
    collector: MetricsCollector, store: MetricsStore, world: World
) -> None:
    """Six hours: the day window is full for six hours, the hour window entirely."""
    run(collector, world, 6 * HOUR)

    hour = query(store, world, HOUR)
    day = query(store, world, DAY)

    assert len(filled(hour)) == len(hour.series[0].points) - 1 or len(filled(hour)) >= 715
    assert day.resolution == "1m"
    assert len(filled(day)) >= 6 * 60 - 2
    assert len(filled(day)) < 6 * 60 + 5
    peaks = [peak for _ts, _mean, peak in day.series[0].points if peak is not None]
    assert set(peaks) == {20.0}


def test_a_collector_that_was_down_leaves_a_hole_and_never_bridges_it(
    collector: MetricsCollector, store: MetricsStore, world: World
) -> None:
    """An hour recorded, twenty minutes of nothing, an hour again: the gap is null."""
    run(collector, world, HOUR)
    world.tick(20 * 60)
    run(collector, world, HOUR)

    result = query(store, world, 3 * HOUR)

    points = result.series[0].points
    gap_start = START + HOUR + 60
    gap_end = START + HOUR + 20 * 60 - 60
    inside = [mean for ts, mean, _peak in points if gap_start <= ts <= gap_end]
    assert inside and all(value is None for value in inside)
    before = [mean for ts, mean, _peak in points if START + 10 * 60 < ts < START + HOUR - 60]
    after = [mean for ts, mean, _peak in points if ts > START + HOUR + 21 * 60]
    assert all(value == 20.0 for value in before)
    assert all(value == 20.0 for value in after if value is not None)


def test_a_spike_is_visible_in_the_month_view(
    collector: MetricsCollector,
    store: MetricsStore,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five minutes at 95% amid an idle day: the mean hides it, the maximum does not."""
    readings = {"cpu": 5.0}
    fake = SimpleNamespace(
        cpu_percent=lambda interval=None: readings["cpu"],
        virtual_memory=lambda: SimpleNamespace(used=1, total=2, percent=50.0),
        swap_memory=lambda: SimpleNamespace(used=0, total=0, percent=0.0),
        disk_usage=lambda path: SimpleNamespace(used=1, total=2, percent=50.0),
        net_io_counters=lambda: None,
    )
    monkeypatch.setattr("noust.monitor.sampler.psutil", fake)
    run(collector, world, 3 * HOUR)
    readings["cpu"] = 95.0
    run(collector, world, 5 * 60)
    readings["cpu"] = 5.0
    run(collector, world, 2 * HOUR)
    store.consolidate()

    month = query(store, world, 30 * DAY)

    means = [mean for _ts, mean, _peak in month.series[0].points if mean is not None]
    peaks = [peak for _ts, _mean, peak in month.series[0].points if peak is not None]
    assert max(means) < 20.0, "an hourly mean flattens five minutes of load"
    assert max(peaks) == 95.0


def test_the_collector_holds_its_tick_grid_however_the_loop_drifts(
    collector: MetricsCollector, store: MetricsStore, world: World
) -> None:
    """Uneven sleeps do not put two ticks in one cell or leave a cell empty."""
    for gap in (5.2, 4.9, 5.1, 4.8, 5.3, 5.0, 4.7, 5.2):
        world.tick(gap)
        collector.sample_once()

    stamps = [
        row[0]
        for row in store._get_connection().execute("SELECT DISTINCT ts FROM samples ORDER BY ts")
    ]

    assert all(later - earlier == 5 for earlier, later in pairwise(stamps))
    assert collector_module.DEFAULT_INTERVAL_SECONDS == 5.0
