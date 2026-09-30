# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the collector behind the charts and the console's live feed.

Everything here drives :meth:`MetricsCollector.sample_once` with an injected
monotonic clock, a fake psutil, a fake cgroup tree in the test's own directory
and planners that need no systemd, so no test depends on the machine it runs
on. What is defended:

- **Rates are deltas, not readings.** Network bytes per second and per-app CPU
  percent are computed from counter deltas over the injected clock's elapsed
  time; a first sample has no delta and must publish no rate.
- **A unit without a cgroup costs nothing.** Stopped units, cgroup v1 and
  containers simply lack the files; that application is skipped for the tick,
  with no error and no invented zero.
- **Operational failures do not stop the tick, and are not silent.** The
  collector runs unattended; a failure is logged, kept as the last error on the
  lease row, and the next tick runs.
- **One collector at a time.** The lease is taken on start, the daemon takes it
  from the console, and a collector that lost it stops sampling.
- **The live feed works whoever samples**: the console reads the newest values
  from the store when the daemon is the one writing them.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import NoustError
from noust.monitor import collector as collector_module
from noust.monitor.appsampler import read_cpu_usec, read_working_set
from noust.monitor.collector import (
    MetricsCollector,
    recording_status,
)
from noust.monitor.plan import Reason, SamplingPlan, Target
from noust.monitor.sampler import MachineSampler
from noust.monitor.timeseries import MetricsStore, lease_timeout
from noust.web import metrics_collector


class _NoDatabases:
    """No database engines: the collector's tests sample the machine and applications only."""

    def sample(self, now: float | None = None) -> list[tuple[str, float]]:
        return []


#: A fixed wall-clock "now" for the store, so persisted rows have known stamps.
NOW = 1_700_002_800


class FrozenClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = float(now)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class FakePsutil:
    """Stands in for psutil, with counters the test moves by hand."""

    def __init__(self) -> None:
        self.cpu = 12.5
        self.bytes_recv = 10_000
        self.bytes_sent = 20_000
        self.total_memory = 1024 * 1024

    def cpu_percent(self, interval: Any = None) -> float:
        return self.cpu

    def virtual_memory(self) -> Any:
        return SimpleNamespace(used=512 * 1024, total=self.total_memory, percent=50.0)

    def swap_memory(self) -> Any:
        return SimpleNamespace(used=64 * 1024, total=128 * 1024, percent=50.0)

    def disk_usage(self, path: str) -> Any:
        return SimpleNamespace(used=30_000, total=100_000, percent=30.0)

    def net_io_counters(self) -> Any:
        return SimpleNamespace(bytes_recv=self.bytes_recv, bytes_sent=self.bytes_sent)


class FakeApp:
    """An application row, with only what the collector reads off it."""

    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.app_type = "nodejs"


class FakePlanner:
    """Builds one cgroup plan per application, pointing into the test's tree."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.builds = 0
        self.fail: Exception | None = None
        self.reasons: dict[str, Reason] = {}

    def build(self, apps: list[Any]) -> dict[str, SamplingPlan]:
        self.builds += 1
        if self.fail is not None:
            raise self.fail
        plans = {}
        for app in apps:
            reason = self.reasons.get(app.domain)
            plans[app.domain] = SamplingPlan(
                domain=app.domain,
                source="cgroup",
                targets=(Target(name=unit_of(app.domain), cgroup=unit_dir(self.root, app.domain)),),
                reason=reason,
            )
        return plans


def unit_of(domain: str) -> str:
    """The unit an application runs as: named after it, dots as dashes."""
    return domain.replace(".", "-")


def unit_dir(root: Path, domain: str) -> Path:
    """Where one unit's cgroup lives in the fake tree."""
    return root / f"{unit_of(domain)}.service"


def write_cgroup(
    root: Path, domain: str, *, usage_usec: int, memory: int, inactive_file: int | None = None
) -> Path:
    """
    Lay out one unit's cgroup files the way systemd does.

    Args:
        root: The fake cgroup root.
        domain: The application domain.
        usage_usec: Cumulative CPU time for cpu.stat.
        memory: Bytes for memory.current.
        inactive_file: Reclaimable page cache for memory.stat, when given.

    Returns:
        The unit's cgroup directory.
    """
    unit = unit_dir(root, domain)
    unit.mkdir(parents=True, exist_ok=True)
    (unit / "cpu.stat").write_text(
        f"usage_usec {usage_usec}\nuser_usec {usage_usec // 2}\nsystem_usec {usage_usec // 2}\n"
    )
    (unit / "memory.current").write_text(f"{memory}\n")
    if inactive_file is not None:
        (unit / "memory.stat").write_text(f"anon 1\ninactive_file {inactive_file}\nfile 9\n")
    return unit


@pytest.fixture
def clock() -> FrozenClock:
    """Provide a monotonic clock frozen at zero."""
    return FrozenClock()


@pytest.fixture
def store(tmp_path: Path) -> MetricsStore:
    """Provide a metrics store on a throwaway database."""
    return MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)


@pytest.fixture
def fake_psutil(monkeypatch: pytest.MonkeyPatch) -> FakePsutil:
    """Replace the sampler's psutil with counters the test controls."""
    fake = FakePsutil()
    monkeypatch.setattr("noust.monitor.sampler.psutil", fake)
    monkeypatch.setattr("noust.monitor.sampler.os.getloadavg", lambda: (0.42, 0.2, 0.1))
    return fake


@pytest.fixture
def apps() -> list[FakeApp]:
    """The applications the collector will be given; a test edits the list."""
    return []


@pytest.fixture
def planner(tmp_path: Path) -> FakePlanner:
    """A planner that puts every application's unit in the fake cgroup tree."""
    return FakePlanner(tmp_path / "cgroup")


@pytest.fixture
def collector(
    store: MetricsStore,
    clock: FrozenClock,
    fake_psutil: FakePsutil,
    planner: FakePlanner,
    apps: list[FakeApp],
) -> MetricsCollector:
    """Build a collector wired entirely to fakes."""
    return MetricsCollector(
        store,
        databases=_NoDatabases(),
        clock=clock,
        planner=planner,  # type: ignore[arg-type]
        apps_source=lambda: list(apps),
        machine=MachineSampler(apps_root="/tmp", wall_clock=clock),
    )


# ---------------------------------------------------------------------------
# System metrics
# ---------------------------------------------------------------------------


def test_gauges_are_recorded_every_tick(collector: MetricsCollector, store: MetricsStore) -> None:
    """The plain readings land in the store and the snapshot, stamped on the grid."""
    snapshot = collector.sample_once()

    assert snapshot["cpu.percent"] == 12.5
    assert snapshot["mem.used_bytes"] == 512 * 1024
    assert snapshot["swap.used_bytes"] == 64 * 1024
    assert snapshot["disk.used_bytes"] == 30_000
    assert snapshot["load.1m"] == 0.42
    assert store.query("cpu.percent", window_s=60) == [(NOW, 12.5)]


def test_the_totals_are_written_at_first_and_then_only_when_they_change(
    collector: MetricsCollector, clock: FrozenClock, fake_psutil: FakePsutil
) -> None:
    """Total memory and disk size never change: writing them every 5 s is noise."""
    first = collector.sample_once()
    clock.advance(5.0)
    second = collector.sample_once()
    clock.advance(5.0)
    fake_psutil.total_memory *= 2
    third = collector.sample_once()

    assert first["mem.total_bytes"] == 1024 * 1024
    assert first["disk.total_bytes"] == 100_000
    assert first["swap.total_bytes"] == 128 * 1024
    assert "mem.total_bytes" not in second
    assert third["mem.total_bytes"] == 2 * 1024 * 1024


def test_the_totals_come_round_again_on_a_slow_timer(
    collector: MetricsCollector, clock: FrozenClock
) -> None:
    """Each tier keeps a reading to draw a ceiling from."""
    collector.sample_once()
    clock.advance(301.0)

    assert "mem.total_bytes" in collector.sample_once()


def test_network_rates_are_deltas_over_the_injected_clock(
    collector: MetricsCollector, clock: FrozenClock, fake_psutil: FakePsutil
) -> None:
    """bytes_recv grows by 4096 over 2 seconds, so the rate is 2048 B/s."""
    first = collector.sample_once()

    clock.advance(2.0)
    fake_psutil.bytes_recv += 4096
    fake_psutil.bytes_sent += 1024
    second = collector.sample_once()

    assert "net.rx_bytes_s" not in first, "a first sample has no delta to rate"
    assert second["net.rx_bytes_s"] == 2048.0
    assert second["net.tx_bytes_s"] == 512.0


def test_a_counter_reset_does_not_become_a_negative_rate(
    collector: MetricsCollector, clock: FrozenClock, fake_psutil: FakePsutil
) -> None:
    """An interface bounce resets kernel counters; that is a gap, not a spike."""
    collector.sample_once()
    clock.advance(2.0)
    fake_psutil.bytes_recv = 0

    assert "net.rx_bytes_s" not in collector.sample_once()


def test_without_psutil_the_system_is_skipped_and_apps_still_sample(
    collector: MetricsCollector,
    apps: list[FakeApp],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil is an optional extra; its absence must not blank the app charts."""
    monkeypatch.setattr("noust.monitor.sampler.psutil", None)
    apps.append(FakeApp("example.com"))
    write_cgroup(tmp_path / "cgroup", "example.com", usage_usec=0, memory=2048)

    assert collector.sample_once() == {"app.example.com.mem.bytes": 2048.0}


# ---------------------------------------------------------------------------
# Per-application metrics
# ---------------------------------------------------------------------------


def test_app_cpu_percent_is_a_delta_over_the_cgroup_counter(
    collector: MetricsCollector, clock: FrozenClock, tmp_path: Path, apps: list[FakeApp]
) -> None:
    """One second of CPU time over two seconds of wall time is 50 percent."""
    apps.append(FakeApp("example.com"))
    root = tmp_path / "cgroup"
    write_cgroup(root, "example.com", usage_usec=1_000_000, memory=1024)
    first = collector.sample_once()

    clock.advance(2.0)
    write_cgroup(root, "example.com", usage_usec=2_000_000, memory=4096)
    second = collector.sample_once()

    assert "app.example.com.cpu.percent" not in first, "a first sample has no delta"
    assert first["app.example.com.mem.bytes"] == 1024.0
    assert second["app.example.com.cpu.percent"] == 50.0
    assert second["app.example.com.mem.bytes"] == 4096.0


def test_memory_is_the_working_set_not_the_page_cache(
    collector: MetricsCollector, tmp_path: Path, apps: list[FakeApp]
) -> None:
    """memory.current counts reclaimable file cache: Docker subtracts it, and so do we."""
    apps.append(FakeApp("example.com"))
    write_cgroup(
        tmp_path / "cgroup", "example.com", usage_usec=1, memory=10_000, inactive_file=7_000
    )

    assert collector.sample_once()["app.example.com.mem.bytes"] == 3_000.0


def test_an_app_without_a_cgroup_is_skipped_without_error(
    collector: MetricsCollector, tmp_path: Path, apps: list[FakeApp]
) -> None:
    """A stopped unit, cgroup v1 or a container: no files, no metrics, no noise."""
    apps.extend([FakeApp("present.com"), FakeApp("absent.com")])
    write_cgroup(tmp_path / "cgroup", "present.com", usage_usec=500, memory=1024)

    snapshot = collector.sample_once()

    assert "app.present.com.mem.bytes" in snapshot
    assert not any("absent.com" in metric for metric in snapshot)


def test_an_application_with_a_reason_is_not_sampled(
    collector: MetricsCollector,
    planner: FakePlanner,
    tmp_path: Path,
    apps: list[FakeApp],
) -> None:
    """A plan that says why it cannot measure is not read, whatever files exist."""
    apps.extend([FakeApp("stack.example.com"), FakeApp("shop.example.com")])
    write_cgroup(tmp_path / "cgroup", "stack.example.com", usage_usec=1, memory=4096)
    write_cgroup(tmp_path / "cgroup", "shop.example.com", usage_usec=1, memory=8192)
    planner.reasons["stack.example.com"] = Reason(code="compose", message="not read")

    snapshot = collector.sample_once()

    assert "app.stack.example.com.mem.bytes" not in snapshot
    assert snapshot["app.shop.example.com.mem.bytes"] == 8192.0


def test_a_restarted_unit_does_not_produce_a_negative_cpu_rate(
    collector: MetricsCollector, clock: FrozenClock, tmp_path: Path, apps: list[FakeApp]
) -> None:
    """After a restart the counter begins again at zero; that tick has no rate."""
    apps.append(FakeApp("example.com"))
    root = tmp_path / "cgroup"
    write_cgroup(root, "example.com", usage_usec=5_000_000, memory=1024)
    collector.sample_once()

    clock.advance(2.0)
    write_cgroup(root, "example.com", usage_usec=100, memory=1024)

    assert "app.example.com.cpu.percent" not in collector.sample_once()


def test_a_unit_that_disappears_and_returns_starts_its_delta_over(
    collector: MetricsCollector, clock: FrozenClock, tmp_path: Path, apps: list[FakeApp]
) -> None:
    """The old counter is forgotten while the cgroup is gone."""
    apps.append(FakeApp("example.com"))
    root = tmp_path / "cgroup"
    unit = write_cgroup(root, "example.com", usage_usec=1_000_000, memory=1024)
    collector.sample_once()

    clock.advance(2.0)
    (unit / "cpu.stat").unlink()
    collector.sample_once()

    clock.advance(2.0)
    write_cgroup(root, "example.com", usage_usec=9_000_000, memory=1024)

    assert "app.example.com.cpu.percent" not in collector.sample_once()


def test_the_plans_are_rebuilt_on_a_slow_timer(
    collector: MetricsCollector,
    planner: FakePlanner,
    clock: FrozenClock,
    tmp_path: Path,
    apps: list[FakeApp],
) -> None:
    """systemd is asked once every half minute, not once per tick."""
    apps.append(FakeApp("old.com"))
    root = tmp_path / "cgroup"
    write_cgroup(root, "old.com", usage_usec=1, memory=1)
    write_cgroup(root, "new.com", usage_usec=1, memory=1)
    collector.sample_once()

    apps[:] = [FakeApp("new.com")]
    clock.advance(2.0)
    within = collector.sample_once()
    clock.advance(collector_module.APPS_REFRESH_SECONDS)
    after = collector.sample_once()

    assert "app.old.com.mem.bytes" in within
    assert "app.new.com.mem.bytes" not in within
    assert "app.new.com.mem.bytes" in after
    assert "app.old.com.mem.bytes" not in after
    assert planner.builds == 2


# ---------------------------------------------------------------------------
# Failures do not stop the tick, and are not silent
# ---------------------------------------------------------------------------


def test_a_failing_psutil_does_not_stop_the_tick_and_is_remembered(
    collector: MetricsCollector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient system read error costs the reading, not the thread."""

    class Broken(FakePsutil):
        def cpu_percent(self, interval: Any = None) -> float:
            raise OSError("proc went away")

    monkeypatch.setattr("noust.monitor.sampler.psutil", Broken())

    assert collector.sample_once() == {}
    assert collector.last_error is not None
    assert "proc went away" in collector.last_error


def test_a_failing_store_does_not_stop_the_tick(
    collector: MetricsCollector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persistence failing must not take the live snapshot down with it."""

    def refuse(pairs: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(collector.store, "record_many", refuse)

    snapshot = collector.sample_once()

    assert snapshot["cpu.percent"] == 12.5
    assert collector.last_error is not None and "database is locked" in collector.last_error


def test_a_failing_plan_build_keeps_the_previous_plans(
    collector: MetricsCollector,
    planner: FakePlanner,
    clock: FrozenClock,
    tmp_path: Path,
    apps: list[FakeApp],
) -> None:
    """A transient error must not blank every application chart."""
    apps.append(FakeApp("example.com"))
    write_cgroup(tmp_path / "cgroup", "example.com", usage_usec=1, memory=1024)
    collector.sample_once()

    planner.fail = NoustError("systemctl is unavailable")
    clock.advance(collector_module.APPS_REFRESH_SECONDS + 1)
    snapshot = collector.sample_once()

    assert "app.example.com.mem.bytes" in snapshot
    assert collector.last_error is not None and "systemctl is unavailable" in collector.last_error


def test_the_store_is_consolidated_on_its_timer_with_a_lookback(
    collector: MetricsCollector, clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention has no scheduler; the sampling loop drives it, reading only recent rows."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(collector.store, "consolidate", lambda **kwargs: calls.append(kwargs))

    collector.sample_once()
    assert calls == [], "consolidation must wait for its interval"

    clock.advance(collector_module.CONSOLIDATE_SECONDS)
    collector.sample_once()
    assert calls == [{"lookback": collector_module.CONSOLIDATE_LOOKBACK_SECONDS}]

    clock.advance(2.0)
    collector.sample_once()
    assert len(calls) == 1, "consolidation must not run on every tick"


def test_a_tick_is_stamped_on_the_interval_grid(
    collector: MetricsCollector, store: MetricsStore, clock: FrozenClock
) -> None:
    """Every series of a tick shares one timestamp, on a multiple of the interval."""
    collector.sample_once()

    rows = store._get_connection().execute("SELECT DISTINCT ts FROM samples").fetchall()

    assert [row[0] for row in rows] == [NOW]
    assert NOW % 5 == 0


# ---------------------------------------------------------------------------
# The thread, the lease and the snapshot
# ---------------------------------------------------------------------------


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    """Poll until the predicate holds or the timeout passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_start_takes_the_lease_and_stop_gives_it_up(
    collector: MetricsCollector, store: MetricsStore
) -> None:
    """A running collector is the one holding the lease; stopping frees it."""
    collector.interval_s = 0.01
    collector.start()
    thread = collector._thread
    assert thread is not None and thread.is_alive()

    assert wait_for(lambda: store.collector_lease() is not None)
    lease = store.collector_lease()
    assert lease is not None and lease.kind == "daemon"
    assert wait_for(lambda: (store.collector_lease() or lease).ticks > 0)

    collector.start()
    assert collector._thread is thread, "a second start must not spawn a second thread"

    collector.stop()
    assert not thread.is_alive()
    assert collector._thread is None
    assert store.collector_lease() is None
    collector.stop()


def test_a_second_collector_waits_while_the_first_holds_the_lease(
    store: MetricsStore,
    clock: FrozenClock,
    fake_psutil: FakePsutil,
    planner: FakePlanner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two collectors never write the same series: the newcomer polls, it does not sample."""
    monkeypatch.setattr(collector_module, "LEASE_RETRY_SECONDS", 0.01)

    def make(kind: str) -> MetricsCollector:
        return MetricsCollector(
            store,
            databases=_NoDatabases(),
            kind=kind,
            interval_s=0.01,
            clock=clock,
            planner=planner,  # type: ignore[arg-type]
            apps_source=lambda: [],
            machine=MachineSampler(apps_root="/tmp", wall_clock=clock),
        )

    first, second = make("daemon"), make("daemon")
    first.start()
    try:
        assert wait_for(lambda: first.holds_lease)
        second.start()
        time.sleep(0.1)
        assert not second.holds_lease
    finally:
        second.stop()
        first.stop()


def test_the_daemon_takes_the_lease_from_the_console_and_the_console_stops(
    store: MetricsStore,
    clock: FrozenClock,
    fake_psutil: FakePsutil,
    planner: FakePlanner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console samples only until a daemon exists; then it notices and lets go."""
    monkeypatch.setattr(collector_module, "LEASE_RETRY_SECONDS", 0.01)

    def make(kind: str) -> MetricsCollector:
        return MetricsCollector(
            store,
            databases=_NoDatabases(),
            kind=kind,
            interval_s=0.01,
            clock=clock,
            planner=planner,  # type: ignore[arg-type]
            apps_source=lambda: [],
            machine=MachineSampler(apps_root="/tmp", wall_clock=clock),
        )

    console, daemon = make("console"), make("daemon")
    console.start()
    try:
        assert wait_for(lambda: console.holds_lease)
        daemon.start()
        try:
            assert wait_for(lambda: daemon.holds_lease)
            assert wait_for(lambda: not console.holds_lease)
            lease = store.collector_lease()
            assert lease is not None and lease.kind == "daemon"
        finally:
            daemon.stop()
    finally:
        console.stop()


def test_an_unexpected_error_in_a_tick_is_logged_kept_and_survived(
    collector: MetricsCollector,
    store: MetricsStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The tick is an error boundary: a programming error must not end the chart silently."""
    ticks = {"n": 0}
    original = collector.sample_once

    def flaky() -> dict[str, float]:
        ticks["n"] += 1
        if ticks["n"] == 1:
            raise RuntimeError("something nobody planned for")
        return original()

    monkeypatch.setattr(collector, "sample_once", flaky)
    collector.interval_s = 0.01

    with caplog.at_level("ERROR", logger=collector_module.log.name):
        collector.start()
        try:
            assert wait_for(lambda: ticks["n"] >= 3), "the thread died after the first error"
        finally:
            collector.stop()

    assert any("a metrics tick failed" in record.message for record in caplog.records)
    assert any(record.exc_info for record in caplog.records), "the traceback must be logged"


def test_the_error_reaches_the_lease_row(
    collector: MetricsCollector, store: MetricsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why history has holes is readable by whoever asks, not only in the journal."""

    def refuse(pairs: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(collector.store, "record_many", refuse)
    collector.interval_s = 0.01
    collector.start()
    try:
        assert wait_for(
            lambda: (
                (store.collector_lease() is not None)
                and "disk I/O error" in (store.collector_lease().last_error or "")
            )  # type: ignore[union-attr]
        )
    finally:
        collector.stop()


def test_the_first_lease_catches_up_on_consolidation(
    collector: MetricsCollector, store: MetricsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After downtime the buckets that completed meanwhile should not wait five minutes."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(store, "consolidate", lambda **kwargs: calls.append(kwargs))
    collector.interval_s = 0.01
    collector.start()
    try:
        assert wait_for(lambda: calls != [])
    finally:
        collector.stop()

    assert calls[0] == {}, "start-up reads everything, not just a lookback"


def test_the_snapshot_is_a_copy(collector: MetricsCollector) -> None:
    """A caller holding the snapshot must not be able to edit the collector's."""
    collector._holding = True
    collector.sample_once()

    held = collector.latest()
    held["cpu.percent"] = -1.0

    assert collector.latest()["cpu.percent"] == 12.5


def test_the_live_feed_reads_the_store_when_another_process_samples(
    store: MetricsStore, clock: FrozenClock
) -> None:
    """The console's collector is idle while the daemon writes: latest() reads what it wrote."""
    other = MetricsCollector(store, kind="console", clock=clock, databases=_NoDatabases())
    store.record_many([("cpu.percent", 33.0), ("app.a.mem.bytes", 9.0)], ts=NOW - 2)

    assert other.latest() == {"cpu.percent": 33.0, "app.a.mem.bytes": 9.0}


def test_a_stale_reading_is_not_a_live_one(store: MetricsStore, clock: FrozenClock) -> None:
    """Numbers from an hour ago must not be pushed as if they were now."""
    other = MetricsCollector(store, kind="console", clock=clock, databases=_NoDatabases())
    store.record("cpu.percent", 33.0, ts=NOW - 3_600)

    assert other.latest() == {}


def test_the_snapshot_can_be_read_while_the_collector_ticks(
    collector: MetricsCollector, clock: FrozenClock
) -> None:
    """latest() is called from the event loop while the thread samples."""
    errors: list[BaseException] = []
    stop = threading.Event()

    def read_constantly() -> None:
        while not stop.is_set():
            try:
                assert isinstance(collector.latest(), dict)
            except AssertionError as exc:
                errors.append(exc)
                return

    reader = threading.Thread(target=read_constantly)
    reader.start()
    try:
        for _ in range(200):
            clock.advance(2.0)
            collector.sample_once()
    finally:
        stop.set()
        reader.join(timeout=5)

    assert errors == []


# ---------------------------------------------------------------------------
# cgroup files
# ---------------------------------------------------------------------------


def test_cpu_stat_parsing(tmp_path: Path) -> None:
    """The counter is read by name, wherever it sits in the file."""
    stat = tmp_path / "cpu.stat"
    stat.write_text("nr_periods 3\nusage_usec 12345\nuser_usec 900\n")

    assert read_cpu_usec(stat) == 12345


@pytest.mark.parametrize("content", ["", "user_usec 900\n", "usage_usec not-a-number\n"])
def test_cpu_stat_without_a_usable_counter_reads_as_none(tmp_path: Path, content: str) -> None:
    """A malformed file is a skipped metric, never an exception."""
    stat = tmp_path / "cpu.stat"
    stat.write_text(content)

    assert read_cpu_usec(stat) is None


def test_a_missing_cpu_stat_reads_as_none(tmp_path: Path) -> None:
    """The common case on cgroup v1, in containers and for stopped units."""
    assert read_cpu_usec(tmp_path / "cpu.stat") is None


def test_the_working_set_never_goes_below_zero(tmp_path: Path) -> None:
    """A page cache larger than the counter (a race between two reads) reads as zero."""
    (tmp_path / "memory.current").write_text("100\n")
    (tmp_path / "memory.stat").write_text("inactive_file 500\n")

    assert read_working_set(tmp_path) == 0


def test_the_working_set_falls_back_to_the_counter_without_memory_stat(tmp_path: Path) -> None:
    """A cgroup without memory.stat still has memory.current."""
    (tmp_path / "memory.current").write_text("100\n")

    assert read_working_set(tmp_path) == 100
    assert read_working_set(tmp_path / "nowhere") is None


# ---------------------------------------------------------------------------
# Is history being recorded
# ---------------------------------------------------------------------------


def test_a_live_daemon_is_recording(store: MetricsStore) -> None:
    """A lease with a fresh heartbeat: recording, by the daemon, with no reason."""
    store.acquire_collector("m", kind="daemon", interval_s=5)

    status = recording_status(store)

    assert status.recording and status.host == "daemon"
    assert status.reason is None and status.advice is None
    assert status.since == NOW


def test_the_console_recording_alone_carries_advice(store: MetricsStore) -> None:
    """It works, but the history stops when the console does."""
    store.acquire_collector("w", kind="console", interval_s=5)

    status = recording_status(store)

    assert status.recording and status.host == "console"
    assert status.advice is not None and status.advice.code == "console_only"


@pytest.mark.parametrize(
    ("probe", "code"),
    [
        (None, "collector_stopped"),
        ({"installed": False, "enabled": False, "active": False}, "monitor_not_installed"),
        ({"installed": True, "enabled": False, "active": False}, "monitor_disabled"),
        ({"installed": True, "enabled": True, "active": False}, "monitor_not_running"),
        ({"installed": True, "enabled": True, "active": True}, "collector_stalled"),
    ],
)
def test_nothing_recording_says_why_in_order(
    store: MetricsStore, probe: dict[str, bool] | None, code: str
) -> None:
    """The first true reason: not installed, disabled, not running, stalled."""
    status = recording_status(store, monitor=lambda: probe)

    assert not status.recording
    assert status.reason is not None and status.reason.code == code
    assert status.reason.fix


def test_a_stalled_collector_carries_its_last_error(
    store: MetricsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The evidence is the collector's own failure, verbatim."""
    times = {"now": NOW}
    stale = MetricsStore(store.db_path, clock=lambda: times["now"])
    stale.acquire_collector("m", kind="daemon", interval_s=5)
    stale.heartbeat_collector("m", error="OperationalError: disk I/O error")
    times["now"] += lease_timeout(5) + 10

    status = recording_status(
        stale, monitor=lambda: {"installed": True, "enabled": True, "active": True}
    )

    assert not status.recording
    assert status.reason is not None and status.reason.evidence == (
        "OperationalError: disk I/O error"
    )
    assert status.last_sample_at == NOW


# ---------------------------------------------------------------------------
# The process-wide wiring
# ---------------------------------------------------------------------------


def test_start_and_stop_wire_and_clear_the_singleton(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_psutil: FakePsutil,
) -> None:
    """What the lifespan calls: start exposes the collector, stop retires it."""
    monkeypatch.setattr(metrics_collector, "_store", None)
    monkeypatch.setattr(metrics_collector, "_collector", None)
    monkeypatch.setattr(
        metrics_collector,
        "open_store",
        lambda: MetricsStore(tmp_path / "metrics.db"),
    )
    monkeypatch.setattr(collector_module, "_stored_apps", lambda: [])
    monkeypatch.setattr("noust.monitor.collector.PlanBuilder", lambda: FakePlanner(tmp_path))

    started = metrics_collector.start_metrics_collector()
    try:
        assert started is not None
        assert started.kind == "console"
        assert metrics_collector.get_metrics_collector() is started
        assert metrics_collector.start_metrics_collector() is started
    finally:
        metrics_collector.stop_metrics_collector()

    assert metrics_collector.get_metrics_collector() is None


def test_the_console_handle_does_not_import_a_web_framework() -> None:
    """The collector lives in the monitor package, importable without FastAPI."""
    import ast

    source = Path(collector_module.__file__).read_text()
    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) and node.module else ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }

    assert "fastapi" not in imported
    assert "starlette" not in imported
    assert not any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("noust.web")
        for node in ast.walk(ast.parse(source))
    )
