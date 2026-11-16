# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every container uses, in one reading (item 189).

The list of containers draws the processor and the memory of each row, and asking for them one container
at a time would start a process for each row of the page. Docker prints them all in one ``docker stats``
without a stream, so that is asked once, for every container that runs, and each container is given either
its reading or the reason there is none (it is not running, it is restarting, it started a moment ago,
Docker did not say). What Docker prints as the limit of a container that has none is the machine's memory,
which is not a limit. The reading is kept for a few seconds behind the request, so a page that is open and
a few people looking at the same server ask Docker once.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker import stats
from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import AppRecord, Registry
from noust.core.store import NoustStore
from noust.core.teams import Scope
from tests.support.docker_machine import SHOP, DockerMachine

#: Docker prints the machine's memory where a container has no limit.
UNLIMITED = {
    "CPUPerc": "2.00%",
    "MemUsage": "18MiB / 7.6GiB",
    "MemPerc": "0.23%",
    "NetIO": "1kB / 2kB",
    "BlockIO": "0B / 0B",
    "PIDs": "3",
}


@pytest.fixture(autouse=True)
def fresh_readings() -> Iterator[None]:
    stats.forget_readings()
    yield
    stats.forget_readings()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class Clock:
    """A clock a test moves."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def machine() -> DockerMachine:
    box = DockerMachine()
    box.box("web", started_at="2026-11-12T08:00:00Z", memory=512 * 2**20)
    box.box("cache", started_at="2026-11-12T08:00:00Z", stats=UNLIMITED)
    box.box(
        "old-test",
        state="exited",
        status="Exited (0) 41 days ago",
        started_at="2026-10-01T08:00:00Z",
    )
    box.box(
        "api-loop",
        state="restarting",
        status="Restarting (137) 8 seconds ago",
        started_at="2026-11-12T11:59:20Z",
    )
    return box


def gate(box: DockerMachine, store: NoustStore, clock: Clock | None = None, **kwargs: object):
    return DockerGate(
        box,
        store=store,
        registry=lambda: Registry(apps=(AppRecord("shop.example.com", SHOP),)),
        disk=lambda path: None,
        clock=clock or Clock(),
        **kwargs,  # type: ignore[arg-type]
    )


def by_name(found: stats.UsageList) -> dict[str, stats.Usage]:
    return {item.container.name: item.usage for item in found.items}


class TestOneReadingForEveryContainer:
    def test_docker_is_asked_once_for_all_of_them_and_not_once_for_each(
        self, store: NoustStore
    ) -> None:
        box = machine()

        gate(box, store).containers_usage()

        asked = [call for call in box.calls if call[:2] == ("docker", "stats")]
        assert asked == [("docker", "stats", "--no-stream", "--format", "{{json .}}")]

    def test_a_container_that_runs_has_its_reading(self, store: NoustStore) -> None:
        found = by_name(gate(machine(), store).containers_usage())

        web = found["web"]
        assert web.available and web.reason == ""
        assert (web.cpu_percent, web.memory_bytes) == (14.5, 212 * 2**20)
        assert (web.net_rx_bytes, web.block_read_bytes, web.pids) == (38_000, 1_200_000, 12)

    def test_the_machines_memory_is_not_the_limit_of_a_container_that_has_none(
        self, store: NoustStore
    ) -> None:
        found = by_name(gate(machine(), store).containers_usage())

        # ``web`` has a limit of its own and ``cache`` has none: Docker printed 7.6GiB for it.
        assert found["web"].memory_limit_bytes == 512 * 2**20
        assert found["web"].memory_percent == 41.41
        assert found["cache"].available
        assert found["cache"].memory_limit_bytes is None
        assert found["cache"].memory_percent is None

    def test_one_that_does_not_run_says_so_instead_of_showing_zeros(
        self, store: NoustStore
    ) -> None:
        found = by_name(gate(machine(), store).containers_usage())

        assert (found["old-test"].available, found["old-test"].reason) == (False, "not_running")
        assert found["old-test"].cpu_percent is None

    def test_one_that_docker_keeps_restarting_says_so(self, store: NoustStore) -> None:
        found = by_name(gate(machine(), store).containers_usage())

        assert (found["api-loop"].available, found["api-loop"].reason) == (False, "restarting")

    def test_one_that_started_a_moment_ago_has_not_used_anything_yet(
        self, store: NoustStore
    ) -> None:
        clock = Clock()
        box = machine()
        box.box("fresh", started_at="2026-11-12T08:00:00Z")
        # Eight seconds before the clock: the whole container started then.
        from datetime import datetime, timezone

        stamp = datetime.fromtimestamp(clock.now - 8, tz=timezone.utc)
        box.boxes["fresh"].started_at = stamp.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")

        found = by_name(gate(box, store, clock).containers_usage())

        assert (found["fresh"].available, found["fresh"].reason) == (False, "too_young")

    def test_one_that_runs_and_docker_did_not_print_has_no_reading(self, store: NoustStore) -> None:
        box = machine()
        box.script(["docker", "stats"], stdout=_only("web"), exit_code=0)

        found = by_name(gate(box, store).containers_usage())

        assert found["web"].available
        assert (found["cache"].available, found["cache"].reason) == (False, "no_reading")

    def test_a_docker_that_fails_to_measure_gives_every_running_container_no_reading(
        self, store: NoustStore
    ) -> None:
        box = machine()
        box.script(["docker", "stats"], stdout="", stderr="error", exit_code=1)

        found = by_name(gate(box, store).containers_usage())

        assert found["web"].reason == found["cache"].reason == "no_reading"
        # What does not run is still said as what it is.
        assert found["old-test"].reason == "not_running"

    def test_nothing_is_asked_when_nothing_runs(self, store: NoustStore) -> None:
        box = DockerMachine()
        box.box("old-test", state="exited", status="Exited (0) 41 days ago")

        found = gate(box, store).containers_usage()

        assert [call for call in box.calls if call[:2] == ("docker", "stats")] == []
        assert by_name(found)["old-test"].reason == "not_running"

    def test_a_principal_that_does_not_see_a_container_does_not_get_its_reading(
        self, store: NoustStore
    ) -> None:
        box = machine()
        box.box("shop-web-1", project="shop-example-com", working_dir=SHOP)

        everything = by_name(gate(box, store).containers_usage())
        a_team = by_name(
            gate(
                box, store, scope=Scope(team_roles={1: "member"}), sees_app=lambda d: False
            ).containers_usage()
        )

        assert "shop-web-1" in everything
        assert "shop-web-1" not in a_team
        assert "web" not in a_team  # nobody's: the instance's alone


class TestKeptBehindTheRequest:
    def asked(self, box: DockerMachine) -> int:
        return len([call for call in box.calls if call[:2] == ("docker", "stats")])

    def test_a_second_look_a_moment_later_does_not_ask_docker_again(
        self, store: NoustStore
    ) -> None:
        clock, box = Clock(), machine()

        gate(box, store, clock).containers_usage()
        clock.now += stats.KEEP_SECONDS - 1
        gate(box, store, clock).containers_usage()

        assert self.asked(box) == 1

    def test_an_old_reading_is_taken_again(self, store: NoustStore) -> None:
        clock, box = Clock(), machine()

        gate(box, store, clock).containers_usage()
        clock.now += stats.KEEP_SECONDS + 1
        gate(box, store, clock).containers_usage()

        assert self.asked(box) == 2

    def test_the_reading_says_when_it_was_taken(self, store: NoustStore) -> None:
        clock = Clock()

        found = gate(machine(), store, clock).containers_usage()

        assert found.read_at == clock.now

    def test_what_a_machine_said_is_not_given_to_another(self, store: NoustStore) -> None:
        first, second = machine(), DockerMachine()
        second.box("web", stats={**UNLIMITED, "CPUPerc": "99.00%"})

        gate(first, store).containers_usage()
        found = by_name(gate(second, store).containers_usage())

        assert found["web"].cpu_percent == 99.0


def _only(name: str) -> str:
    """What Docker prints when it measured one container and not the rest."""
    import json

    from tests.support.docker_machine import full_id

    return json.dumps(
        {
            "ID": full_id(name)[:12],
            "Name": name,
            "CPUPerc": "14.50%",
            "MemUsage": "212MiB / 512MiB",
            "MemPerc": "41.41%",
            "NetIO": "38kB / 91kB",
            "BlockIO": "1.2MB / 0B",
            "PIDs": "12",
        }
    )
