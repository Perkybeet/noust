# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Each container of a Compose stack has series of its own (checklist G-04).

The stack's total says the disk is busy; these say which service is busy. The
kernel's counters are faked as files in a temporary tree, as in
``test_app_traffic_io.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.monitor.appstatus import container_series_names
from noust.monitor.plan import KIND_COMPOSE, KIND_UNIT, SamplingPlan, Target
from noust.monitor.series import (
    SUFFIXES,
    container_label,
    container_series,
    parse_container_series,
)
from tests.test_app_traffic_io import Rig, values

DOMAIN = "stack.example.com"


class TestTheNames:
    def test_a_name_is_made_of_the_domain_the_segment_and_the_container(self) -> None:
        assert (
            container_series(DOMAIN, "stack-web-1", "cpu.percent")
            == "app.stack.example.com.ctr.stack-web-1.cpu.percent"
        )

    @pytest.mark.parametrize("suffix", SUFFIXES)
    def test_every_name_is_read_back(self, suffix: str) -> None:
        name = container_series(DOMAIN, "stack-web-1", suffix)

        assert parse_container_series(name) == (DOMAIN, "stack-web-1", suffix)

    def test_a_domain_that_contains_the_segment_is_read_from_the_end(self) -> None:
        name = container_series("ctr.example.com", "web", "mem.bytes")

        assert parse_container_series(name) == ("ctr.example.com", "web", "mem.bytes")

    def test_an_applications_own_series_are_not_a_containers(self) -> None:
        for name in (
            "app.stack.example.com.cpu.percent",
            "app.stack.example.com.io.read_bytes_s",
            "cpu.percent",
            "app.stack.example.com.ctr.web.unknown.thing",
        ):
            assert parse_container_series(name) is None

    def test_a_dot_in_a_docker_name_cannot_be_part_of_a_metric_name(self) -> None:
        assert container_label("my.proj_web.1") == "my_proj_web_1"
        assert container_label("a/b c") == "a_b_c"

    def test_a_very_long_name_is_cut(self) -> None:
        assert len(container_label("x" * 500) or "") == 64

    def test_a_container_that_lives_for_one_command_gets_no_series(self) -> None:
        assert container_label("stack-web-run-3f9a1c2b7d41") is None
        assert container_label("stack-web-1") == "stack-web-1"

    def test_nothing_is_left_of_a_name_with_no_usable_character(self) -> None:
        assert container_label("   ") is None


class TestSampling:
    def stack(self, rig: Rig) -> dict[str, SamplingPlan]:
        self.web = rig.unit("stack-web", kind="container")
        self.db = rig.unit("stack-db", kind="container")
        return rig.plan(DOMAIN, self.web, self.db, kind=KIND_COMPOSE)

    def test_each_container_has_its_own_rates_beside_the_total(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        plans = self.stack(rig)
        rig.io(self.web, 0, 0)
        rig.io(self.db, 0, 0)
        rig.sampler.sample(plans, 100.0, None)

        rig.io(self.web, 1_000, 0)
        rig.io(self.db, 3_000, 500)
        got = values(rig.sampler.sample(plans, 110.0, 10.0))

        assert got[f"app.{DOMAIN}.io.read_bytes_s"] == 400.0
        assert got[f"app.{DOMAIN}.ctr.stack-web.io.read_bytes_s"] == 100.0
        assert got[f"app.{DOMAIN}.ctr.stack-db.io.read_bytes_s"] == 300.0
        assert got[f"app.{DOMAIN}.ctr.stack-db.io.write_bytes_s"] == 50.0

    def test_memory_is_there_from_the_first_tick(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        plans = self.stack(rig)

        got = values(rig.sampler.sample(plans, 100.0, None))

        assert got[f"app.{DOMAIN}.ctr.stack-web.mem.bytes"] == 1.0
        assert got[f"app.{DOMAIN}.ctr.stack-db.mem.bytes"] == 1.0
        assert got[f"app.{DOMAIN}.mem.bytes"] == 2.0

    def test_cpu_is_the_containers_own_and_the_total_is_their_sum(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        plans = self.stack(rig)
        rig.sampler.sample(plans, 100.0, None)
        (self.web.cgroup / "cpu.stat").write_text("usage_usec 1000001\n")  # type: ignore[operator]
        (self.db.cgroup / "cpu.stat").write_text("usage_usec 3000001\n")  # type: ignore[operator]

        got = values(rig.sampler.sample(plans, 110.0, 10.0))

        assert got[f"app.{DOMAIN}.ctr.stack-web.cpu.percent"] == pytest.approx(10.0)
        assert got[f"app.{DOMAIN}.ctr.stack-db.cpu.percent"] == pytest.approx(30.0)
        assert got[f"app.{DOMAIN}.cpu.percent"] == pytest.approx(40.0)

    def test_a_container_that_cannot_be_read_has_no_series_and_the_other_still_does(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        plans = self.stack(rig)
        (self.db.cgroup / "memory.current").unlink()  # type: ignore[operator]

        got = values(rig.sampler.sample(plans, 100.0, None))

        assert f"app.{DOMAIN}.ctr.stack-web.mem.bytes" in got
        assert f"app.{DOMAIN}.ctr.stack-db.mem.bytes" not in got

    def test_a_one_off_container_is_in_the_total_but_has_no_series_of_its_own(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path)
        run = rig.unit("stack-web-run-3f9a1c2b7d41", kind="container")
        plans = rig.plan(DOMAIN, self.stack(rig)[DOMAIN].targets[0], run, kind=KIND_COMPOSE)

        got = values(rig.sampler.sample(plans, 100.0, None))

        assert got[f"app.{DOMAIN}.mem.bytes"] == 2.0
        assert not [name for name in got if "run-3f9a1c2b7d41" in name]

    def test_a_unit_has_no_container_series(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        plans = rig.plan("shop.example.com", rig.unit("shop.example.com"), kind=KIND_UNIT)

        got = values(rig.sampler.sample(plans, 100.0, None))

        assert not [name for name in got if ".ctr." in name]


class TestTheStatus:
    def test_a_stack_names_the_series_of_each_container(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        web = rig.unit("stack-web", kind="container")
        plan = rig.plan(DOMAIN, web, kind=KIND_COMPOSE)[DOMAIN]

        (entry,) = container_series_names(plan)

        assert entry["name"] == "stack-web"
        assert entry["series"]["cpu"] == f"app.{DOMAIN}.ctr.stack-web.cpu.percent"
        assert entry["series"]["disk_read"] == f"app.{DOMAIN}.ctr.stack-web.io.read_bytes_s"
        assert set(entry["series"]) == {
            "cpu",
            "memory",
            "disk_read",
            "disk_write",
            "net_rx",
            "net_tx",
        }

    def test_a_target_without_a_cgroup_is_left_out(self) -> None:
        plan = SamplingPlan(
            domain=DOMAIN,
            kind=KIND_COMPOSE,
            targets=(Target(name="stack-web", kind="container", cgroup=None),),
        )

        assert container_series_names(plan) == []

    def test_anything_that_is_not_a_stack_has_none(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path)
        plan = rig.plan("shop.example.com", rig.unit("shop.example.com"))["shop.example.com"]

        assert container_series_names(plan) == []
