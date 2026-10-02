# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the per-minute process samples and the monitor's unit events.

A chart says the CPU peaked; these rows say what was running. What is defended:

- **Top five by CPU and top five by memory**, CPU measured as the difference of
  each process's CPU seconds over the time that passed (the first minute only
  primes), a process that started since the last minute counted from zero, a
  reused pid never mistaken for the process it replaced.
- **Every row names its owner**: the Noust application (through its unit, its
  Compose container or its PHP-FPM pool), else the systemd unit, else nothing.
- **A command line never carries a secret**: options, ``NAME=value`` pairs,
  MySQL's glued ``-p``, tokens on their own and URL passwords are replaced.
- **Stored in the metrics database for as long as the per-minute metrics**,
  once per minute by the collector, under the minute the CPU was measured over.
- **Unit failures and recoveries the monitor announces are kept** beside them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.config import REDACTED
from noust.core.runner import FakeRunner
from noust.monitor.collector import MetricsCollector
from noust.monitor.plan import SamplingPlan, Target
from noust.monitor.process_samples import (
    RANK_CPU,
    RANK_MEMORY,
    OwnerIndex,
    ProcessReading,
    ProcessSample,
    ProcessSampler,
    cgroup_path,
    redact_command,
)
from noust.monitor.timeseries import (
    EVENT_UNIT_FAILED,
    EVENT_UNIT_RECOVERED,
    MINUTE,
    MetricsStore,
)
from tests.test_monitor_units import make_monitor, scan, show_block
from tests.test_notifier import config  # noqa: F401  (pytest resolves fixtures by name)

# ruff: noqa: F811

NOW = 1_700_002_800
MOUNT = Path("/sys/fs/cgroup")


def reading(
    pid: int,
    name: str,
    *,
    cpu: float = 0.0,
    memory: int = 1_000,
    created: float = 1.0,
    cmdline: tuple[str, ...] = (),
    user: str = "www-data",
) -> ProcessReading:
    """One process as the reader would see it."""
    return ProcessReading(
        pid=pid,
        name=name,
        user=user,
        cpu_seconds=cpu,
        memory_bytes=memory,
        memory_percent=memory / 10_000,
        create_time=created,
        cmdline=cmdline or (name,),
    )


class Table:
    """A process table the test changes between two samples."""

    def __init__(self, rows: list[ProcessReading]) -> None:
        self.rows = rows

    def __call__(self) -> list[ProcessReading]:
        return list(self.rows)


def cgroups(mapping: dict[int, str]) -> Callable[[int], str]:
    """A cgroup reader answering ``0::<path>`` for the pids given."""
    return lambda pid: f"0::{mapping[pid]}\n" if pid in mapping else ""


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("node", "server.js", "--port", "3000"), "node server.js --port 3000"),
        (("app", "--password=hunter2", "--user=bob"), f"app --password={REDACTED} --user=bob"),
        (("app", "--api-key", "abc123", "--verbose"), f"app --api-key {REDACTED} --verbose"),
        (("mysql", "-uroot", "-psup3rs3cret", "shop"), f"mysql -uroot -p{REDACTED} shop"),
        (("env", "DB_PASS=hunter2", "PORT=80"), f"env DB_PASS={REDACTED} PORT=80"),
        (
            ("worker", "postgres://shop:hunter2@db:5432/shop"),
            f"worker postgres://shop:{REDACTED}@db:5432/shop",
        ),
        (("deploy", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"), f"deploy {REDACTED}"),
    ],
)
def test_a_command_line_keeps_no_secret(argv: tuple[str, ...], expected: str) -> None:
    """Every shape a secret routinely takes in an argv is replaced."""
    assert redact_command(argv) == expected


def test_a_long_command_line_is_cut_with_an_ellipsis() -> None:
    """The stored line is bounded, and says it was cut."""
    line = redact_command(("python3", "-c", "x" * 500), limit=40)

    assert line is not None
    assert len(line) == 40
    assert line.endswith("…")


def test_a_kernel_thread_has_no_command_line() -> None:
    """No argv is no line, never an empty string."""
    assert redact_command(()) is None


# ---------------------------------------------------------------------------
# Owners
# ---------------------------------------------------------------------------


def plans() -> dict[str, SamplingPlan]:
    """An application on a unit, a Compose stack and a PHP-FPM pool."""
    return {
        "shop.example.com": SamplingPlan(
            domain="shop.example.com",
            source="cgroup",
            targets=(
                Target(
                    name="shop-example-com.service",
                    cgroup=MOUNT / "system.slice/shop-example-com.service",
                    control_group="/system.slice/shop-example-com.service",
                ),
            ),
        ),
        "stack.example.com": SamplingPlan(
            domain="stack.example.com",
            source="docker",
            targets=(
                Target(
                    name="stack-web-1",
                    kind="container",
                    cgroup=MOUNT / "system.slice/docker-abc123.scope",
                ),
            ),
        ),
        "blog.example.com": SamplingPlan(
            domain="blog.example.com", source="fpm", pool="blog-example-com"
        ),
    }


def test_a_process_in_an_application_s_unit_is_that_application() -> None:
    """The unit's cgroup and anything nested in it belong to the application."""
    index = OwnerIndex(plans())

    owner = index.owner_of("0::/system.slice/shop-example-com.service/worker\n", "node")

    assert (owner.app, owner.kind, owner.name) == (
        "shop.example.com",
        "unit",
        "shop-example-com.service",
    )


def test_a_container_of_a_stack_is_the_stack_s_application() -> None:
    """A Compose container is named by its container, under its application."""
    owner = OwnerIndex(plans()).owner_of("0::/system.slice/docker-abc123.scope\n", "postgres")

    assert (owner.app, owner.kind, owner.name) == ("stack.example.com", "container", "stack-web-1")


def test_a_php_fpm_worker_is_its_pool_s_application() -> None:
    """FPM workers share the FPM unit; their pool tells the applications apart."""
    owner = OwnerIndex(plans()).owner_of(
        "0::/system.slice/php8.3-fpm.service\n", "php-fpm8.3", ("php-fpm: pool blog-example-com",)
    )

    assert (owner.app, owner.kind, owner.name) == ("blog.example.com", "pool", "blog-example-com")


def test_anything_else_is_its_unit_or_nothing() -> None:
    """A system unit is named; a login shell or a kernel thread belongs to nothing."""
    index = OwnerIndex(plans())

    assert index.owner_of("0::/system.slice/nginx.service\n", "nginx").name == "nginx.service"
    assert index.owner_of("0::/system.slice/nginx.service\n", "nginx").app is None
    assert index.owner_of("", "kworker/0:1").kind is None


def test_the_unified_hierarchy_wins_on_a_hybrid_machine() -> None:
    """cgroup v1 lines are listed first on a hybrid machine; the v2 path is the one read."""
    text = "12:cpu,cpuacct:/system.slice/a.service\n0::/system.slice/b.service\n"

    assert cgroup_path(text) == "/system.slice/b.service"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_the_first_sample_only_primes_the_cpu_counters() -> None:
    """Without a previous reading there is no CPU over a minute to rank by."""
    sampler = ProcessSampler(reader=Table([reading(1, "node", cpu=5.0)]), wall_clock=lambda: NOW)

    assert sampler.sample(NOW - 60, now=0.0) == []


def test_the_top_five_by_cpu_and_by_memory_are_kept_with_their_owners() -> None:
    """CPU is the difference of CPU seconds over the elapsed time, 100 per core."""
    rows = [reading(pid, f"p{pid}", cpu=10.0, memory=pid * 1_000) for pid in range(1, 9)]
    table = Table(rows)
    sampler = ProcessSampler(
        reader=table,
        cgroup_reader=cgroups({7: "/system.slice/shop-example-com.service"}),
        wall_clock=lambda: NOW,
    )
    sampler.sample(NOW - 120, now=0.0)
    # Over 60 seconds, process n used n * 3 seconds: n * 5 percent.
    table.rows = [
        reading(pid, f"p{pid}", cpu=10.0 + pid * 3, memory=pid * 1_000) for pid in range(1, 9)
    ]

    samples = sampler.sample(NOW - 60, now=60.0, plans=plans())

    by_cpu = [s for s in samples if s.rank == RANK_CPU]
    by_memory = [s for s in samples if s.rank == RANK_MEMORY]
    assert [(s.pid, s.cpu_percent, s.position) for s in by_cpu] == [
        (8, 40.0, 1),
        (7, 35.0, 2),
        (6, 30.0, 3),
        (5, 25.0, 4),
        (4, 20.0, 5),
    ]
    assert [s.pid for s in by_memory] == [8, 7, 6, 5, 4]
    assert all(s.ts == NOW - 60 for s in samples)
    seven = next(s for s in by_cpu if s.pid == 7)
    assert (seven.app, seven.owner) == ("shop.example.com", "shop-example-com.service")


def test_a_process_born_since_the_last_minute_counts_all_its_cpu() -> None:
    """A build started thirty seconds ago used everything it shows in this minute."""
    table = Table([reading(1, "sshd", cpu=1.0, created=10.0)])
    wall = [NOW - 60.0]
    sampler = ProcessSampler(reader=table, wall_clock=lambda: wall[0])
    sampler.sample(NOW - 120, now=0.0)
    wall[0] = NOW
    table.rows = [
        reading(1, "sshd", cpu=1.0, created=10.0),
        reading(2, "node", cpu=30.0, created=NOW - 30.0, cmdline=("node", "build.js")),
    ]

    samples = sampler.sample(NOW - 60, now=60.0)

    top = next(s for s in samples if s.rank == RANK_CPU)
    assert (top.pid, top.cpu_percent, top.command) == (2, 50.0, "node build.js")


def test_a_reused_pid_is_not_the_process_it_replaced() -> None:
    """Same pid, another start time: not a negative delta, not a stolen history."""
    table = Table([reading(5, "old", cpu=500.0, created=100.0)])
    sampler = ProcessSampler(reader=table, wall_clock=lambda: NOW)
    sampler.sample(NOW - 120, now=0.0)
    # Born before the previous sample by the wall clock, yet never seen: unknown, skipped.
    table.rows = [reading(5, "new", cpu=2.0, created=200.0)]

    samples = sampler.sample(NOW - 60, now=60.0)

    assert [s for s in samples if s.rank == RANK_CPU] == []
    assert [s.name for s in samples if s.rank == RANK_MEMORY] == ["new"]


def test_a_stored_command_line_is_redacted() -> None:
    """The secret never reaches the database."""
    table = Table([reading(1, "app", cpu=0.0, cmdline=("app", "--token=s3cr3t"))])
    sampler = ProcessSampler(reader=table, wall_clock=lambda: NOW)
    sampler.sample(NOW - 120, now=0.0)
    table.rows = [reading(1, "app", cpu=6.0, cmdline=("app", "--token=s3cr3t"))]

    samples = sampler.sample(NOW - 60, now=60.0)

    assert {s.command for s in samples} == {f"app --token={REDACTED}"}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def sample(ts: int, rank: str, position: int, **fields: object) -> ProcessSample:
    """A stored row with defaults."""
    values: dict[str, object] = {
        "pid": 100 + position,
        "name": "node",
        "user": "app",
        "cpu_percent": 10.0,
        "memory_bytes": 1_000,
        "memory_percent": 0.1,
    }
    values.update(fields)
    return ProcessSample(ts=ts, rank=rank, position=position, **values)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> MetricsStore:
    """A metrics store on a throwaway database, with a fixed clock."""
    return MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)


def test_samples_are_read_back_by_minute_and_by_application(store: MetricsStore) -> None:
    """A stretch reads its minutes in order; an application reads only its rows."""
    store.record_process_samples(
        [
            sample(NOW - 120, RANK_CPU, 1, app="shop.example.com"),
            sample(NOW - 120, RANK_MEMORY, 1),
            sample(NOW - 60, RANK_CPU, 1, app="shop.example.com", command="node x"),
        ]
    )

    everything = store.process_samples(NOW - 600, NOW)
    shop = store.process_samples(NOW - 600, NOW, app="shop.example.com")

    assert [(s.ts, s.rank) for s in everything] == [
        (NOW - 120, RANK_CPU),
        (NOW - 120, RANK_MEMORY),
        (NOW - 60, RANK_CPU),
    ]
    assert [s.ts for s in shop] == [NOW - 120, NOW - 60]
    assert shop[1].command == "node x"
    assert store.first_process_sample_at() == NOW - 120


def test_a_minute_written_twice_keeps_the_newer_ranking(store: MetricsStore) -> None:
    """A restarted collector that ranks the same minute again replaces it."""
    store.record_process_samples([sample(NOW - 60, RANK_CPU, 1, name="old")])
    store.record_process_samples([sample(NOW - 60, RANK_CPU, 1, name="new")])

    assert [s.name for s in store.process_samples(NOW - 60, NOW)] == ["new"]


def test_samples_live_as_long_as_the_minute_metrics(store: MetricsStore) -> None:
    """Retention: what the minute tier keeps, the processes behind it keep."""
    keep = NOW - (MINUTE.retention or 0) + 60
    drop = NOW - (MINUTE.retention or 0) - 60
    store.record_process_samples([sample(drop, RANK_CPU, 1), sample(keep, RANK_CPU, 1)])

    store.consolidate(now=NOW)

    assert [s.ts for s in store.process_samples(0, NOW)] == [keep]


def test_nothing_sampled_says_so(store: MetricsStore) -> None:
    """No history at all is None, so the timeline can say since when there is some."""
    assert store.first_process_sample_at() is None


def test_monitor_events_are_kept_and_read_by_stretch(store: MetricsStore) -> None:
    """A unit's failure and recovery, oldest first, only inside the stretch."""
    store.record_monitor_event(EVENT_UNIT_FAILED, "a.service", detail="failed", ts=NOW - 900)
    store.record_monitor_event(EVENT_UNIT_RECOVERED, "a.service", ts=NOW - 300)
    store.record_monitor_event(EVENT_UNIT_FAILED, "b.service", ts=NOW - 7_200)

    events = store.monitor_events(NOW - 3_600, NOW)

    assert [(e.kind, e.subject) for e in events] == [
        (EVENT_UNIT_FAILED, "a.service"),
        (EVENT_UNIT_RECOVERED, "a.service"),
    ]


# ---------------------------------------------------------------------------
# The collector
# ---------------------------------------------------------------------------


class _Nothing:
    """A sampler of machine, applications or databases that reads nothing."""

    def sample(self, *args: object, **kwargs: object) -> list[tuple[str, float]]:
        return []


class _Planner:
    def build(self, apps: list[object]) -> dict[str, SamplingPlan]:
        return {}


def test_the_collector_ranks_processes_once_a_minute_under_the_minute_measured(
    tmp_path: Path,
) -> None:
    """Twelve ticks of five seconds are one ranking, stored under the minute that ended."""
    wall = [float(NOW)]
    mono = [0.0]
    table = Table([reading(1, "node", cpu=0.0)])
    store = MetricsStore(tmp_path / "metrics.db", clock=lambda: wall[0])
    collector = MetricsCollector(
        store,
        clock=lambda: mono[0],
        planner=_Planner(),  # type: ignore[arg-type]
        apps_source=lambda: [],
        machine=_Nothing(),  # type: ignore[arg-type]
        app_sampler=_Nothing(),  # type: ignore[arg-type]
        databases=_Nothing(),  # type: ignore[arg-type]
        processes=ProcessSampler(reader=table, wall_clock=lambda: wall[0]),
    )

    for tick in range(13):
        table.rows = [reading(1, "node", cpu=tick * 3.0)]
        collector.sample_once()
        wall[0] += 5
        mono[0] += 5

    rows = store.process_samples(0, NOW + 3_600)
    assert [(r.ts, r.rank) for r in rows] == [(NOW, RANK_CPU), (NOW, RANK_MEMORY)]
    assert rows[0].cpu_percent == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------


def test_the_monitor_keeps_a_unit_failure_and_its_recovery(config: object, tmp_path: Path) -> None:
    """What it announces once, it also writes down beside the metrics."""
    runner = FakeRunner()
    monitor, _ = make_monitor(runner, ["shop-example-com"])
    store = MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)
    monitor._metrics = SimpleNamespace(store=store)  # type: ignore[assignment]

    scan(
        runner,
        monitor,
        show_block("shop-example-com.service", active="failed", result="exit-code", status=1),
    )
    scan(
        runner,
        monitor,
        show_block("shop-example-com.service", active="failed", result="exit-code", status=1),
    )
    scan(runner, monitor, show_block("shop-example-com.service"))

    events = store.monitor_events(0, NOW)
    assert [(e.kind, e.subject) for e in events] == [
        (EVENT_UNIT_FAILED, "shop-example-com"),
        (EVENT_UNIT_RECOVERED, "shop-example-com"),
    ]
    assert events[0].reason == "failed"
