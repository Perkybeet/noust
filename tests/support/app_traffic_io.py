# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_traffic_io.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

from noust.core.runner import FakeRunner
from noust.monitor.appio import UnitNetwork
from noust.monitor.appsampler import AppSampler
from noust.monitor.plan import KIND_UNIT, SOURCE_CGROUP, SamplingPlan, Target
from noust.monitor.timeseries import MetricsStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


NOW = 1_800_000_000


class Rig:
    def __init__(self, tmp_path: Path, with_network: bool = False) -> None:
        self.store = MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)
        self.runner = FakeRunner()
        self.clock = Clock()
        self.network = (
            UnitNetwork(lambda: self.runner, interval_s=15.0, clock=self.clock)
            if with_network
            else None
        )
        self.sampler = AppSampler(self.store, unit_network=self.network)
        self.root = tmp_path / "cgroup"

    def unit(self, domain: str, *, kind: str = "unit") -> Target:
        directory = self.root / domain
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cpu.stat").write_text("usage_usec 1\n")
        (directory / "memory.current").write_text("1\n")
        return Target(name=domain.replace(".", "-"), kind=kind, cgroup=directory)

    def plan(self, domain: str, *targets: Target, kind: str = KIND_UNIT) -> dict[str, SamplingPlan]:
        return {
            domain: SamplingPlan(
                domain=domain, kind=kind, source=SOURCE_CGROUP, targets=tuple(targets)
            )
        }

    def io(self, target: Target, read: int, written: int) -> None:
        assert target.cgroup is not None
        (target.cgroup / "io.stat").write_text(
            f"8:0 rbytes={read} wbytes={written} rios=1 wios=1\n"
        )


def values(pairs: list[tuple[str, float]]) -> dict[str, float]:
    return dict(pairs)
