# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container Docker keeps restarting is announced, and what Docker did to the stacks is kept (G-09).

What is pinned: the restart counter is a baseline taken the first time a container is seen, a
container that restarted ``monitor.container_restart_limit`` times since is announced once with its
exit code and last lines, a quiet five minutes closes it, a recreated container starts again, the
limit can be turned off, a container that is not a Noust stack's is nobody's business, and the
events Docker kept (a death, an out-of-memory kill, an unhealthy check) are stored beside the
metrics with the application they belong to.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.notifications.model import State
from noust.core.store import App, AppType
from noust.monitor.watch_containers import (
    ContainerWatch,
    describe_event,
    parse_inspect,
)
from tests.monitor_rig import NOW, Rig

CID = "a" * 64
OTHER = "b" * 64


def inspect_line(cid: str, *, restarts: int, status: str = "running", code: int = 1) -> str:
    return f"{cid}|/shop-web-1|{restarts}|{status}|{code}|false\n"


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    instance.store.create_app(
        App(
            domain="shop.example.com",
            app_path="/srv/shop",
            port=3000,
            app_type=AppType.DOCKER_COMPOSE.value,
            compose_project="shop",
        )
    )
    yield instance
    instance.close()


class Kept:
    """A metrics store that remembers what was recorded in it."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_monitor_event(self, kind: str, subject: str, **kwargs: Any) -> None:
        self.events.append({"kind": kind, "subject": subject, **kwargs})


def show(rig: Rig, *, restarts: int, status: str = "running", extra: str = "") -> None:
    rig.runner.script(
        ("docker", "ps"),
        stdout=f"{CID}\tshop-web-1\trunning\tshop\t\t/srv/shop\n{extra}",
    )
    rig.runner.script(
        ("docker", "inspect"), stdout=inspect_line(CID, restarts=restarts, status=status)
    )
    rig.runner.script(("docker", "logs"), stderr="Error: ECONNREFUSED 10.0.0.1:5432")
    rig.runner.script(("docker", "events"), stdout="")


def watch(**kwargs: Any) -> ContainerWatch:
    return ContainerWatch(**kwargs)


def test_the_first_time_a_container_is_seen_its_counter_is_a_baseline(rig: Rig) -> None:
    show(rig, restarts=40)

    watch().check(rig.services)

    assert rig.sent == []


def test_restarts_past_the_limit_since_it_was_seen_are_announced_once(rig: Rig) -> None:
    show(rig, restarts=2)
    watch().check(rig.services)
    show(rig, restarts=7)
    rig.now += 60

    watch().check(rig.services)
    watch().check(rig.services)

    [message] = rig.sent
    assert message.kind == "container_restart_loop"
    assert message.code == "container.restart_loop"
    assert message.state is State.FAILED
    assert message.domain == "shop.example.com"
    assert "5" in message.summary
    assert message.excerpt is not None
    assert "ECONNREFUSED" in "\n".join(message.excerpt.lines)


def test_fewer_restarts_than_the_limit_say_nothing(rig: Rig) -> None:
    show(rig, restarts=0)
    watch().check(rig.services)
    show(rig, restarts=4)

    watch().check(rig.services)

    assert rig.sent == []


def test_the_limit_is_the_operators(tmp_path: Path) -> None:
    rig = Rig(tmp_path, monitor__container_restart_limit=2)
    try:
        rig.store.create_app(
            App(
                domain="shop.example.com",
                app_path="/srv/shop",
                port=3000,
                app_type=AppType.DOCKER_COMPOSE.value,
                compose_project="shop",
            )
        )
        show(rig, restarts=0)
        watch().check(rig.services)
        show(rig, restarts=2)

        watch().check(rig.services)

        assert rig.codes() == ["container.restart_loop"]
    finally:
        rig.close()


def test_a_limit_of_zero_turns_the_check_off(tmp_path: Path) -> None:
    rig = Rig(tmp_path, monitor__container_restart_limit=0)
    try:
        rig.store.create_app(
            App(
                domain="shop.example.com",
                app_path="/srv/shop",
                port=3000,
                app_type=AppType.DOCKER_COMPOSE.value,
                compose_project="shop",
            )
        )
        show(rig, restarts=0)
        watch().check(rig.services)
        show(rig, restarts=500)

        watch().check(rig.services)

        assert rig.sent == []
    finally:
        rig.close()


def test_five_quiet_minutes_close_the_announcement(rig: Rig) -> None:
    show(rig, restarts=0)
    watch().check(rig.services)
    show(rig, restarts=6)
    rig.now += 60
    watch().check(rig.services)
    rig.now += 120
    watch().check(rig.services)
    assert rig.codes() == ["container.restart_loop"]

    rig.now += 400
    watch().check(rig.services)

    assert rig.codes() == ["container.restart_loop", "container.recovered"]
    assert rig.sent[-1].state is State.OK


def test_a_recreated_container_starts_counting_again(rig: Rig) -> None:
    show(rig, restarts=9)
    watch().check(rig.services)
    show(rig, restarts=0)
    rig.now += 60
    watch().check(rig.services)
    show(rig, restarts=3)
    rig.now += 60

    watch().check(rig.services)

    assert rig.sent == []


def test_a_container_that_is_not_a_noust_stacks_is_not_asked_about(rig: Rig) -> None:
    show(rig, restarts=0, extra=f"{OTHER}\tpg\trunning\tother\t\t/srv/other\n")

    watch().check(rig.services)

    inspected = [call for call in rig.runner.calls if call[:2] == ("docker", "inspect")]
    assert OTHER not in inspected[0]
    assert CID in inspected[0]


def test_without_docker_nothing_is_asked(rig: Rig) -> None:
    rig.runner.only_knows("systemctl")

    watch().check(rig.services)

    assert rig.runner.calls == []


def test_docker_failing_to_list_changes_nothing(rig: Rig) -> None:
    rig.runner.script(("docker", "ps"), exit_code=1, stderr="Cannot connect to the Docker daemon")

    watch().check(rig.services)

    assert rig.sent == []


def test_what_docker_did_since_the_last_look_is_kept_for_the_timeline(rig: Rig) -> None:
    show(rig, restarts=0)
    kept = Kept()
    events = "\n".join(
        json.dumps(line)
        for line in (
            {
                "Action": "die",
                "time": NOW - 20,
                "Actor": {"Attributes": {"name": "shop-web-1", "exitCode": "137"}},
            },
            {
                "Action": "oom",
                "time": NOW - 20,
                "Actor": {"Attributes": {"name": "shop-web-1"}},
            },
            {
                "Action": "health_status: healthy",
                "time": NOW - 10,
                "Actor": {"Attributes": {"name": "shop-web-1"}},
            },
            {
                "Action": "die",
                "time": NOW - 5,
                "Actor": {"Attributes": {"name": "somebody-elses", "exitCode": "1"}},
            },
        )
    )
    rig.runner.script(("docker", "events"), stdout=events)

    watch(metrics=lambda: kept).check(rig.services)

    assert [(e["reason"], e["app"], e["subject"]) for e in kept.events] == [
        ("die", "shop.example.com", "shop-web-1"),
        ("oom", "shop.example.com", "shop-web-1"),
    ]
    assert kept.events[0]["detail"] == "shop-web-1 exited with code 137."
    assert kept.events[0]["kind"] == "docker_event"
    assert kept.events[0]["ts"] == int(NOW - 20)


def test_the_events_are_asked_from_where_the_last_look_ended(rig: Rig) -> None:
    show(rig, restarts=0)
    watch(metrics=lambda: Kept()).check(rig.services)
    rig.now += 60

    watch(metrics=lambda: Kept()).check(rig.services)

    asks = [call for call in rig.runner.calls if call[:2] == ("docker", "events")]
    assert asks[1][asks[1].index("--since") + 1] == str(int(NOW))
    assert asks[1][asks[1].index("--until") + 1] == str(int(NOW + 60))


def test_the_events_worth_keeping() -> None:
    def event(action: str, **attributes: str) -> dict[str, Any]:
        return {"Action": action, "Actor": {"Attributes": {"name": "web", **attributes}}}

    assert describe_event(event("die", exitCode="1")) == ("web", "die", "web exited with code 1.")
    assert describe_event(event("oom"))[1] == "oom"  # type: ignore[index]
    assert describe_event(event("restart"))[1] == "restart"  # type: ignore[index]
    assert describe_event(event("health_status: unhealthy"))[1] == "health_status"  # type: ignore[index]
    assert describe_event(event("health_status: healthy")) is None
    assert describe_event(event("create")) is None
    assert describe_event({"Action": "die", "Actor": {}}) is None


def test_the_inspect_lines_are_read_and_the_foreign_or_broken_ones_skipped() -> None:
    text = (
        f"{CID}|/web|3|running|0|false\n"
        f"{OTHER}|/db|1|running|0|false\n"
        "garbage\n"
        f"{CID}|/web|x|running|0|false\n"
    )

    [reading] = parse_inspect(text, {CID: "shop.example.com"})

    assert (reading.name, reading.restarts, reading.domain, reading.oom) == (
        "web",
        3,
        "shop.example.com",
        False,
    )
