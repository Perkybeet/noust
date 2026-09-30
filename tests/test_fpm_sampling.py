# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for measuring a PHP application through its FPM pool.

FPM runs every pool's workers in one service, so the cgroup cannot tell the
applications apart. The pool is told from the title FPM gives each worker
(``php-fpm: pool <name>``): the one process title the monitor reads, and only to
say whose worker it is. What is defended: memory is the sum of the pool's
workers (proportional set size, so the shared opcache is counted once), CPU is
what they used since the last tick including workers born in between, another
pool's workers are not counted, and the first pass measures nothing it cannot
rate.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.monitor.appsampler import AppSampler
from noust.monitor.plan import SamplingPlan, Target
from noust.monitor.timeseries import MetricsStore

NOW = 1_700_002_800


class FakeProcess:
    """A psutil process as the sampler reads it."""

    table: dict[int, FakeProcess] = {}

    def __init__(self, pid: int) -> None:
        if pid not in self.table:
            import psutil

            raise psutil.NoSuchProcess(pid)
        self.state = self.table[pid]

    @classmethod
    def add(cls, pid: int, title: str, *, cpu: float, pss: int, created: float = 1.0) -> None:
        state = SimpleNamespace(title=title, cpu=cpu, pss=pss, created=created)
        instance = object.__new__(cls)
        instance.state = state
        cls.table[pid] = instance

    def create_time(self) -> float:
        return self.state.created

    def cmdline(self) -> list[str]:
        return [self.state.title] if self.state.title else []

    def cpu_times(self) -> SimpleNamespace:
        return SimpleNamespace(user=self.state.cpu / 2, system=self.state.cpu / 2)

    def memory_full_info(self) -> SimpleNamespace:
        return SimpleNamespace(pss=self.state.pss)

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=self.state.pss * 2)


class FakePsutilModule:
    Error = Exception

    class NoSuchProcess(Exception):
        pass

    @staticmethod
    def Process(pid: int) -> FakeProcess:
        if pid not in FakeProcess.table:
            raise FakePsutilModule.NoSuchProcess(pid)
        return FakeProcess.table[pid]


@pytest.fixture
def workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake FPM cgroup with a master and workers of two pools."""
    FakeProcess.table = {}
    monkeypatch.setattr("noust.monitor.appsampler.psutil", FakePsutilModule)
    monkeypatch.setattr(
        "noust.monitor.appsampler._PROCESS_ERRORS", (FakePsutilModule.NoSuchProcess, OSError)
    )
    cgroup = tmp_path / "php8.3-fpm.service"
    cgroup.mkdir()
    FakeProcess.add(100, "php-fpm: master process (/etc/php/8.3/fpm/php-fpm.conf)", cpu=9.0, pss=1)
    FakeProcess.add(101, "php-fpm: pool noust-blog-example-com", cpu=10.0, pss=1_000)
    FakeProcess.add(102, "php-fpm: pool noust-blog-example-com", cpu=20.0, pss=2_000)
    FakeProcess.add(103, "php-fpm: pool noust-shop-example-com", cpu=5.0, pss=5_000)
    (cgroup / "cgroup.procs").write_text("100\n101\n102\n103\n")
    return cgroup


def plan(cgroup: Path, domain: str, pool: str) -> SamplingPlan:
    """A PHP application's plan."""
    return SamplingPlan(
        domain=domain,
        kind="php_fpm",
        source="fpm",
        targets=(Target(name="php8.3-fpm", cgroup=cgroup),),
        pool=pool,
        fpm_service="php8.3-fpm",
    )


@pytest.fixture
def sampler(tmp_path: Path) -> AppSampler:
    """An app sampler on a throwaway store."""
    return AppSampler(MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW))


def test_memory_is_the_sum_of_the_pools_workers_and_only_its_own(
    workers: Path, sampler: AppSampler
) -> None:
    """Another pool's workers and the master are not this application's."""
    plans = {
        "blog.example.com": plan(workers, "blog.example.com", "noust-blog-example-com"),
        "shop.example.com": plan(workers, "shop.example.com", "noust-shop-example-com"),
    }

    pairs = dict(sampler.sample(plans, 1.0, None))

    assert pairs["app.blog.example.com.mem.bytes"] == 3_000.0
    assert pairs["app.shop.example.com.mem.bytes"] == 5_000.0


def test_the_first_pass_has_no_cpu_rate_and_the_second_measures_the_delta(
    workers: Path, sampler: AppSampler
) -> None:
    """Workers already running when the collector starts are not a burst."""
    plans = {"blog.example.com": plan(workers, "blog.example.com", "noust-blog-example-com")}
    first = dict(sampler.sample(plans, 1.0, None))

    FakeProcess.table[101].state.cpu = 12.0
    FakeProcess.table[102].state.cpu = 21.0
    second = dict(sampler.sample(plans, 6.0, 5.0))

    assert "app.blog.example.com.cpu.percent" not in first
    assert second["app.blog.example.com.cpu.percent"] == pytest.approx((2.0 + 1.0) / 5.0 * 100)


def test_a_worker_born_between_ticks_counts_all_its_cpu(workers: Path, sampler: AppSampler) -> None:
    """PHP-FPM spawns workers under load: exactly when CPU matters, and pids are new."""
    plans = {"blog.example.com": plan(workers, "blog.example.com", "noust-blog-example-com")}
    sampler.sample(plans, 1.0, None)

    FakeProcess.add(104, "php-fpm: pool noust-blog-example-com", cpu=2.0, pss=500)
    (workers / "cgroup.procs").write_text("100\n101\n102\n103\n104\n")
    second = dict(sampler.sample(plans, 6.0, 5.0))

    assert second["app.blog.example.com.cpu.percent"] == pytest.approx(2.0 / 5.0 * 100)
    assert second["app.blog.example.com.mem.bytes"] == 3_500.0


def test_a_worker_that_exits_does_not_produce_a_negative_rate(
    workers: Path, sampler: AppSampler
) -> None:
    """pm.max_requests recycles workers; the survivors' delta is all there is."""
    plans = {"blog.example.com": plan(workers, "blog.example.com", "noust-blog-example-com")}
    sampler.sample(plans, 1.0, None)

    (workers / "cgroup.procs").write_text("100\n101\n103\n")
    del FakeProcess.table[102]
    second = dict(sampler.sample(plans, 6.0, 5.0))

    assert second["app.blog.example.com.cpu.percent"] == 0.0
    assert second["app.blog.example.com.mem.bytes"] == 1_000.0


def test_a_pool_with_no_worker_has_no_series(workers: Path, sampler: AppSampler) -> None:
    """Nothing to sum is not zero: the pool may be stopped."""
    plans = {"gone.example.com": plan(workers, "gone.example.com", "noust-gone-example-com")}

    assert sampler.sample(plans, 1.0, None) == []


def test_a_cgroup_that_cannot_be_listed_is_skipped(sampler: AppSampler, tmp_path: Path) -> None:
    """The FPM service vanished under us."""
    plans = {"blog.example.com": plan(tmp_path / "nope", "blog.example.com", "noust-blog")}

    assert sampler.sample(plans, 1.0, None) == []


def test_the_worker_title_is_read_once_per_process(workers: Path, sampler: AppSampler) -> None:
    """The pool of a pid is remembered, so a tick does not re-read every command line."""
    reads = {"n": 0}
    original = FakeProcess.cmdline

    def counting(self: FakeProcess) -> list[str]:
        reads["n"] += 1
        return original(self)

    FakeProcess.cmdline = counting  # type: ignore[method-assign]
    try:
        plans = {"blog.example.com": plan(workers, "blog.example.com", "noust-blog-example-com")}
        sampler.sample(plans, 1.0, None)
        first = reads["n"]
        sampler.sample(plans, 6.0, 5.0)
    finally:
        FakeProcess.cmdline = original  # type: ignore[method-assign]

    assert first == 4
    assert reads["n"] == first


def test_the_fpm_cgroup_is_read_once_per_tick_however_many_pools_share_it(
    workers: Path, sampler: AppSampler
) -> None:
    """Twenty PHP sites in one FPM are one pass over its workers, not twenty."""
    plans = {
        f"site{i}.example.com": plan(workers, f"site{i}.example.com", f"noust-site{i}")
        for i in range(20)
    }
    reads = {"n": 0}
    original = Path.read_text

    def counting(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "cgroup.procs":
            reads["n"] += 1
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    Path.read_text = counting  # type: ignore[method-assign]
    try:
        sampler.sample(plans, 1.0, None)
    finally:
        Path.read_text = original  # type: ignore[method-assign]

    assert reads["n"] == 1
