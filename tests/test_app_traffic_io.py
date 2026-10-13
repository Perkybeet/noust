# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Disk and network traffic per application and per container.

Checklist G-04: ``io.stat`` of the cgroup for what an application reads and
writes, the container's network namespace for what a Compose service moves, and
systemd's IP accounting for a unit. The kernel's counters are faked as files in a
temporary tree; nothing here reads the machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.monitor.appio import (
    UnitNetwork,
    parse_net_dev,
    read_container_network,
    read_io_bytes,
)
from noust.monitor.appsampler import AppSampler
from noust.monitor.appstatus import series_names
from noust.monitor.plan import (
    KIND_COMPOSE,
    KIND_PHP_FPM,
    KIND_STATIC,
    KIND_UNIT,
    SOURCE_CGROUP,
    SamplingPlan,
    Target,
)
from noust.monitor.timeseries import MetricsStore

NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 9999999    1000    0    0    0     0          0         0  9999999    1000    0    0    0     0       0          0
  eth0: 1000000    2000    0    0    0     0          0         0   500000    1500    0    0    0     0       0          0
  eth1:  200000     300    0    0    0     0          0         0    50000     100    0    0    0     0       0          0
"""


class TestIoStat:
    def test_the_devices_are_summed(self, tmp_path: Path) -> None:
        (tmp_path / "io.stat").write_text(
            "8:0 rbytes=1000 wbytes=2000 rios=3 wios=4 dbytes=0 dios=0\n"
            "259:0 rbytes=500 wbytes=250 rios=1 wios=1 dbytes=0 dios=0\n"
        )

        assert read_io_bytes(tmp_path) == (1500, 2250)

    def test_a_cgroup_that_has_done_no_io_reads_zero(self, tmp_path: Path) -> None:
        (tmp_path / "io.stat").write_text("")

        assert read_io_bytes(tmp_path) == (0, 0)

    def test_without_the_controller_there_is_nothing(self, tmp_path: Path) -> None:
        assert read_io_bytes(tmp_path) is None

    def test_junk_in_the_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "io.stat").write_text("8:0 rbytes=abc wbytes=7\nnonsense\n")

        assert read_io_bytes(tmp_path) == (0, 7)


class TestNetDev:
    def test_the_loopback_is_not_traffic(self) -> None:
        assert parse_net_dev(NET_DEV) == (1_200_000, 550_000)

    def test_an_empty_or_broken_table_is_zero(self) -> None:
        assert parse_net_dev("") == (0, 0)
        assert parse_net_dev("  eth0: short line\n") == (0, 0)


class TestContainerNetwork:
    HOST = "net:[4026531840]"

    def tree(self, tmp_path: Path, *, container_ns: str, pid: int = 4242) -> tuple[Path, Path]:
        proc = tmp_path / "proc"
        for name, namespace in (("self", self.HOST), (str(pid), container_ns)):
            (proc / name / "ns").mkdir(parents=True)
            os.symlink(namespace, proc / name / "ns" / "net")
        (proc / str(pid) / "net").mkdir()
        (proc / str(pid) / "net" / "dev").write_text(NET_DEV)
        cgroup = tmp_path / "cgroup" / "docker-abc"
        cgroup.mkdir(parents=True)
        (cgroup / "cgroup.procs").write_text(f"{pid}\n{pid + 1}\n")
        return cgroup, proc

    def test_a_container_in_a_namespace_of_its_own_is_read_through_its_first_process(
        self, tmp_path: Path
    ) -> None:
        cgroup, proc = self.tree(tmp_path, container_ns="net:[4026532999]")

        assert read_container_network(cgroup, proc) == (1_200_000, 550_000)

    def test_a_process_in_the_hosts_namespace_would_report_the_whole_machine(
        self, tmp_path: Path
    ) -> None:
        cgroup, proc = self.tree(tmp_path, container_ns=self.HOST)

        assert read_container_network(cgroup, proc) is None

    def test_a_container_with_no_process_has_no_reading(self, tmp_path: Path) -> None:
        cgroup, proc = self.tree(tmp_path, container_ns="net:[1]")
        (cgroup / "cgroup.procs").write_text("")

        assert read_container_network(cgroup, proc) is None

    def test_a_process_that_exited_between_the_two_reads_is_not_fatal(self, tmp_path: Path) -> None:
        cgroup, proc = self.tree(tmp_path, container_ns="net:[1]")
        (proc / "4242" / "net" / "dev").unlink()

        assert read_container_network(cgroup, proc) is None


def show(*units: tuple[str, str, str]) -> str:
    return "\n\n".join(
        f"Id={name}.service\nIPIngressBytes={rx}\nIPEgressBytes={tx}" for name, rx, tx in units
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestUnitNetwork:
    def make(self, runner: FakeRunner, clock: Clock) -> UnitNetwork:
        return UnitNetwork(lambda: runner, interval_s=15.0, clock=clock)

    def test_one_command_answers_for_every_unit(self) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "show"], stdout=show(("shop", "100", "50"), ("blog", "7", "3")))
        network = self.make(runner, Clock())

        counters = network.counters(["shop", "blog.service", "shop"])

        assert counters == {"shop": (100, 50), "blog": (7, 3)}
        assert network.fresh
        assert runner.calls == [
            (
                "systemctl",
                "show",
                "blog.service",
                "shop.service",
                "--property=Id,IPIngressBytes,IPEgressBytes",
            )
        ]

    def test_a_unit_that_is_not_counting_is_absent(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["systemctl", "show"],
            stdout=show(("shop", "[not set]", "[not set]"), ("blog", str(2**64 - 1), "5")),
        )

        assert self.make(runner, Clock()).counters(["shop", "blog"]) == {}

    def test_it_does_not_ask_again_before_its_interval(self) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "show"], stdout=show(("shop", "100", "50")))
        clock = Clock()
        network = self.make(runner, clock)
        network.counters(["shop"])

        clock.now += 14
        again = network.counters(["shop"])
        assert (again, network.fresh, len(runner.calls)) == ({"shop": (100, 50)}, False, 1)

        clock.now += 1
        network.counters(["shop"])
        assert (network.fresh, len(runner.calls)) == (True, 2)

    def test_systemd_that_cannot_be_asked_says_nothing(self) -> None:
        runner = FakeRunner(default_exit_code=1)

        assert self.make(runner, Clock()).counters(["shop"]) == {}

    def test_no_units_no_question(self) -> None:
        runner = FakeRunner()

        assert self.make(runner, Clock()).counters([]) == {}
        assert runner.calls == []


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


class TestSamplingDiskTraffic:
    def test_the_second_tick_has_the_rate_and_the_first_has_none(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        target = rig.unit("shop.example.com")
        plans = rig.plan("shop.example.com", target)
        rig.io(target, 1_000, 5_000)

        first = values(rig.sampler.sample(plans, 100.0, None))
        rig.io(target, 1_000 + 5 * 2_048, 5_000 + 5 * 512)
        second = values(rig.sampler.sample(plans, 105.0, 5.0))

        assert "app.shop.example.com.io.read_bytes_s" not in first
        assert second["app.shop.example.com.io.read_bytes_s"] == 2_048.0
        assert second["app.shop.example.com.io.write_bytes_s"] == 512.0

    def test_a_stack_is_the_sum_of_its_containers(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        web, db = rig.unit("stack-web", kind="container"), rig.unit("stack-db", kind="container")
        plans = rig.plan("stack.example.com", web, db, kind=KIND_COMPOSE)
        rig.io(web, 0, 0)
        rig.io(db, 0, 0)
        rig.sampler.sample(plans, 100.0, None)

        rig.io(web, 1_000, 0)
        rig.io(db, 3_000, 500)
        got = values(rig.sampler.sample(plans, 110.0, 10.0))

        assert got["app.stack.example.com.io.read_bytes_s"] == 400.0
        assert got["app.stack.example.com.io.write_bytes_s"] == 50.0

    def test_a_counter_that_went_backwards_is_not_a_rate(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        target = rig.unit("shop.example.com")
        plans = rig.plan("shop.example.com", target)
        rig.io(target, 10_000, 10_000)
        rig.sampler.sample(plans, 100.0, None)

        rig.io(target, 10, 10)  # the unit restarted
        got = values(rig.sampler.sample(plans, 105.0, 5.0))

        assert "app.shop.example.com.io.read_bytes_s" not in got
        rig.io(target, 110, 110)
        again = values(rig.sampler.sample(plans, 110.0, 5.0))
        assert again["app.shop.example.com.io.read_bytes_s"] == 20.0

    def test_without_the_io_controller_there_is_no_series_and_cpu_still_flows(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        plans = rig.plan("shop.example.com", rig.unit("shop.example.com"))

        got = values(rig.sampler.sample(plans, 100.0, None))

        assert "app.shop.example.com.mem.bytes" in got
        assert not [name for name in got if ".io." in name]


class TestSamplingNetworkTraffic:
    def test_a_unit_gets_its_rate_from_two_answers_of_systemd(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, with_network=True)
        target = rig.unit("shop.example.com")
        plans = rig.plan("shop.example.com", target)
        rig.runner.script(["systemctl", "show"], stdout=show(("shop-example-com", "1000", "500")))
        first = values(rig.sampler.sample(plans, 1000.0, None))
        assert not [name for name in first if ".net." in name]

        rig.clock.now += 20
        rig.runner.script(
            ["systemctl", "show"], stdout=show(("shop-example-com", str(1000 + 20 * 100), "700"))
        )
        second = values(rig.sampler.sample(plans, 1020.0, 5.0))

        assert second["app.shop.example.com.net.rx_bytes_s"] == 100.0
        assert second["app.shop.example.com.net.tx_bytes_s"] == 10.0

    def test_the_rate_is_repeated_between_two_questions_then_dropped(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, with_network=True)
        target = rig.unit("shop.example.com")
        plans = rig.plan("shop.example.com", target)
        rig.runner.script(["systemctl", "show"], stdout=show(("shop-example-com", "0", "0")))
        rig.sampler.sample(plans, 1000.0, None)
        rig.clock.now += 20
        rig.runner.script(["systemctl", "show"], stdout=show(("shop-example-com", "2000", "0")))
        rig.sampler.sample(plans, 1020.0, 5.0)

        rig.clock.now += 5  # too soon to ask again
        between = values(rig.sampler.sample(plans, 1025.0, 5.0))
        assert between["app.shop.example.com.net.rx_bytes_s"] == 100.0

        rig.clock.now += 100  # systemd stopped answering for long enough
        rig.runner.script(["systemctl", "show"], stdout="", exit_code=1)
        late = values(rig.sampler.sample(plans, 1125.0, 5.0))
        assert "app.shop.example.com.net.rx_bytes_s" not in late

    def test_a_sampler_without_a_unit_network_never_starts_a_process(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        plans = rig.plan("shop.example.com", rig.unit("shop.example.com"))

        rig.sampler.sample(plans, 1000.0, None)

        assert rig.runner.calls == []


class TestWhatTheConsoleIsTold:
    def test_an_application_with_a_cgroup_of_its_own_has_the_four_roles(self) -> None:
        names = series_names(SamplingPlan(domain="shop.example.com", kind=KIND_UNIT))

        assert names["disk_read"] == "app.shop.example.com.io.read_bytes_s"
        assert names["net_tx"] == "app.shop.example.com.net.tx_bytes_s"

    @pytest.mark.parametrize("kind", [KIND_STATIC, KIND_PHP_FPM])
    def test_a_site_without_a_process_of_its_own_has_none(self, kind: str) -> None:
        names = series_names(SamplingPlan(domain="site.example.com", kind=kind))

        assert not [role for role in names if role.startswith(("disk", "net"))]
