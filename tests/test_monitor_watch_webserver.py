# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The web server is watched, and restarted when it falls (the gap the parity audit found beside E-44).

Every site of every application goes through nginx (or Apache). Its unit was not among the ones
the monitor watched, so the day it fell the applications were each "unreachable" one at a time and
nothing started it again. What is pinned: a crash is announced once and the server restarted
after its own configuration test passes, a configuration that does not pass is reported with the
test's words and not restarted, the restarts in an hour are limited, a server stopped cleanly by
a person is announced and left alone, a server that is disabled is nobody's outage, and the
server coming back closes the episode.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.notification_state import NotificationState
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification, State
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.monitor.watch_webserver import WebServerWatch
from noust.monitor.watches import WatchServices

NOW = 1_800_000_000.0


class Settings:
    def __init__(self, **values: Any) -> None:
        self.values = {key.replace("__", "."): value for key, value in values.items()}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def properties(
    unit: str = "nginx",
    *,
    active: str = "active",
    sub: str = "running",
    result: str = "success",
    enabled: str = "enabled",
    load: str = "loaded",
) -> str:
    return (
        f"Id={unit}.service\nLoadState={load}\nActiveState={active}\nSubState={sub}\n"
        f"Result={result}\nExecMainStatus=0\nNRestarts=0\nUnitFileState={enabled}\n"
    )


class Rig:
    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db")
        self.now = NOW
        self.sent: list[Notification] = []
        self.runner = FakeRunner()
        self.context = NotificationContext(locale="en", server="web-1", public_url="")
        self.services = WatchServices(
            publish=lambda build: self.sent.append(build(self.context)),
            state=NotificationState(self.store, clock=lambda: self.now),
            runner=self.runner,
            config=Settings(**settings),  # type: ignore[arg-type]
            clock=lambda: self.now,
        )
        self.watch = WebServerWatch(servers=lambda services: ["nginx"])

    def unit(self, **kwargs: Any) -> None:
        self.runner.script(("systemctl", "show"), stdout=properties(**kwargs))

    def restarts(self) -> int:
        return sum(1 for call in self.runner.calls if call[:2] == ("systemctl", "restart"))


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    yield instance
    NoustStore.reset_instance()


def crashed(rig: Rig) -> None:
    rig.unit(active="failed", sub="failed", result="exit-code")
    rig.runner.script(("nginx", "-t"), stderr="nginx: configuration file test is successful")


def test_a_running_server_says_nothing(rig: Rig) -> None:
    rig.unit()

    rig.watch.check(rig.services)

    assert rig.sent == []
    assert rig.restarts() == 0


def test_a_crashed_server_is_restarted_after_its_test_passes_and_announced(rig: Rig) -> None:
    crashed(rig)

    rig.watch.check(rig.services)

    assert rig.restarts() == 1
    assert ("nginx", "-t") in rig.runner.calls
    [message] = rig.sent
    assert message.kind == "unit_failed"
    assert message.code == "webserver.restarted"
    assert message.state is State.WARNING
    assert message.subject == "nginx"


def test_the_test_runs_before_the_restart(rig: Rig) -> None:
    crashed(rig)

    rig.watch.check(rig.services)

    argv = [
        call
        for call in rig.runner.calls
        if call[:1] == ("nginx",) or call[:2] == ("systemctl", "restart")
    ]
    assert argv[0] == ("nginx", "-t")


def test_a_configuration_that_fails_its_test_is_reported_with_its_words_and_not_restarted(
    rig: Rig,
) -> None:
    rig.unit(active="failed", sub="failed", result="exit-code")
    rig.runner.script(
        ("nginx", "-t"),
        stderr='nginx: [emerg] unknown directive "servre_name" in /etc/nginx/sites-enabled/a:3',
        exit_code=1,
    )

    rig.watch.check(rig.services)

    assert rig.restarts() == 0
    [message] = rig.sent
    assert message.code == "webserver.config_invalid"
    assert message.state is State.FAILED
    assert message.excerpt is not None
    assert "unknown directive" in "\n".join(message.excerpt.lines)


def test_a_failure_that_goes_on_is_announced_once(rig: Rig) -> None:
    rig.unit(active="failed", sub="failed", result="exit-code")
    rig.runner.script(("nginx", "-t"), stderr="[emerg] broken", exit_code=1)

    rig.watch.check(rig.services)
    rig.now += 60
    rig.watch.check(rig.services)

    assert len(rig.sent) == 1


def test_a_restart_that_fails_is_announced_as_failed(rig: Rig) -> None:
    crashed(rig)
    rig.runner.script(("systemctl", "restart"), exit_code=1, stderr="Job for nginx.service failed")

    rig.watch.check(rig.services)

    [message] = rig.sent
    assert message.code == "webserver.restart_failed"
    assert message.state is State.FAILED


def test_the_restarts_in_an_hour_are_limited_and_the_limit_is_said_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path, monitor__webserver_restart_limit=2)
    try:
        crashed(rig)
        for _ in range(5):
            rig.watch.check(rig.services)
            rig.now += 60

        assert rig.restarts() == 2
        assert [m.code for m in rig.sent] == [
            "webserver.restarted",
            "webserver.restarted",
            "webserver.limit",
        ]

        rig.now += 3600
        rig.watch.check(rig.services)
        assert rig.restarts() == 3
    finally:
        NoustStore.reset_instance()


def test_a_server_the_operator_asked_to_stop_is_announced_and_left_alone(rig: Rig) -> None:
    rig.unit(active="inactive", sub="dead", result="success")

    rig.watch.check(rig.services)
    rig.now += 60
    rig.watch.check(rig.services)

    assert rig.restarts() == 0
    [message] = rig.sent
    assert message.code == "webserver.stopped"


def test_a_server_that_does_not_start_at_boot_is_nobodys_outage(rig: Rig) -> None:
    rig.unit(active="inactive", sub="dead", result="success", enabled="disabled")

    rig.watch.check(rig.services)

    assert rig.sent == []
    assert rig.restarts() == 0


def test_a_unit_that_is_not_installed_is_skipped(rig: Rig) -> None:
    rig.unit(active="inactive", load="not-found", result="")

    rig.watch.check(rig.services)

    assert rig.sent == []


def test_a_server_that_comes_back_by_itself_closes_the_episode(rig: Rig) -> None:
    rig.unit(active="inactive", sub="dead", result="success")
    rig.watch.check(rig.services)
    rig.unit()
    rig.now += 60

    rig.watch.check(rig.services)

    assert [m.code for m in rig.sent] == ["webserver.stopped", "unit.recovered"]
    assert rig.sent[-1].state is State.OK


def test_the_automatic_restart_can_be_turned_off_and_the_fall_is_still_announced(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, monitor__restart_webserver=False)
    try:
        crashed(rig)

        rig.watch.check(rig.services)

        assert rig.restarts() == 0
        [message] = rig.sent
        assert message.code == "webserver.down"
        assert message.state is State.FAILED
    finally:
        NoustStore.reset_instance()


def test_the_check_can_be_turned_off(tmp_path: Path) -> None:
    rig = Rig(tmp_path, monitor__webserver_check_seconds=0)
    try:
        assert rig.watch.interval_s(rig.services) == 0
    finally:
        NoustStore.reset_instance()


def test_each_server_is_judged_on_its_own(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    try:
        rig.watch = WebServerWatch(servers=lambda services: ["nginx", "apache"])
        rig.runner.script(
            ("systemctl", "show", "--no-pager", "-p"),
            stdout=properties("nginx")
            + "\n"
            + properties("apache2", active="failed", sub="failed", result="exit-code"),
        )
        rig.runner.script(("apache2ctl", "configtest"), stderr="Syntax OK")

        rig.watch.check(rig.services)

        assert [call[2:] for call in rig.runner.calls if call[:2] == ("systemctl", "restart")] == [
            ("apache2",)
        ]
    finally:
        NoustStore.reset_instance()
