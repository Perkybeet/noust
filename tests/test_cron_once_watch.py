# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the monitor does with a one-time task (item 164).

The hook of the task's own service records and retires it the moment its command ends, and says so
when it failed. The monitor is the net under that: a task whose hook did not arrive (the program
moved, the machine went down in the middle) is settled and announced on the next look, a task is
never reported as a timer that stopped because its timer ends by design, and a task that is still
running is left until it has ended.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.cron_once_history import OnceRun
from noust.core.exceptions import ServiceError
from noust.core.notification_state import NotificationState
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification, State
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.cron_manager import CronOutcome
from noust.monitor.watch_cron import CronWatch
from noust.monitor.watches import WatchServices


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db")
        self.sent: list[Notification] = []
        self.context = NotificationContext(locale="en", server="web-1", public_url="")
        self.services = WatchServices(
            publish=lambda build: self.sent.append(build(self.context)),
            state=NotificationState(self.store, clock=lambda: 1000.0),
            runner=FakeRunner(),
            config=Config(),
            clock=lambda: 1000.0,
        )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    try:
        yield instance
    finally:
        NoustStore.reset_instance()


def ran(result: str = "success", exit_code: int | None = 0) -> OnceRun:
    return OnceRun(
        name="reindex",
        command="/usr/bin/php artisan search:reindex",
        run_at="2026-12-01T14:30:00+01:00",
        result=result,
        app_domain="shop.example.com",
        exit_code=exit_code,
        finished_at="2026-12-01T13:31:10+00:00",
        output="ERROR: disk full\n" if result != "success" else "",
    )


class Cron:
    """A cron manager that answers what a test scripts."""

    def __init__(self) -> None:
        self.outcomes: list[CronOutcome] = []
        self.settled: list[str] = []
        self.run = ran()
        self.gone = False

    def last_outcomes(self) -> list[CronOutcome]:
        return list(self.outcomes)

    def settle_once(self, name: str, **given: Any) -> OnceRun:
        if self.gone:
            raise ServiceError(f"Unknown cron job: {name}")
        self.settled.append(name)
        return self.run

    def runs(self, name: str, **given: Any) -> list[dict[str, Any]]:
        return []


def once_outcome(**extra: Any) -> CronOutcome:
    fields: dict[str, Any] = {
        "name": "reindex",
        "invocation": "inv-1",
        "result": "success",
        "exit_code": 0,
        "once": True,
        "timer": "inactive",
    }
    fields.update(extra)
    return CronOutcome(**fields)


def watch(cron: Cron) -> CronWatch:
    return CronWatch(cron)  # type: ignore[arg-type]


def test_a_task_that_ran_and_was_not_settled_is_settled_and_announced(rig: Rig) -> None:
    cron = Cron()
    cron.run = ran("exit-code", 2)
    cron.outcomes = [once_outcome(result="exit-code", exit_code=2)]

    watch(cron).check(rig.services)

    assert cron.settled == ["reindex"]
    (note,) = rig.sent
    assert (note.kind, note.code, note.state) == ("cron_failed", "cron.failed", State.FAILED)
    assert note.subject == "reindex" and note.domain == "shop.example.com"
    assert note.excerpt is not None and "ERROR: disk full" in note.excerpt.lines
    # Its units are gone: the command to run is the history, not the runs of a task that is not.
    assert note.command is not None and note.command.value == "noust cron history"


def test_a_task_that_succeeded_is_a_heartbeat_with_its_own_switch(rig: Rig) -> None:
    cron = Cron()
    cron.outcomes = [once_outcome()]

    watch(cron).check(rig.services)

    (note,) = rig.sent
    assert (note.kind, note.state) == ("cron_success", State.OK)


def test_a_task_whose_timer_ended_is_not_a_timer_that_stopped(rig: Rig) -> None:
    cron = Cron()
    cron.outcomes = [once_outcome(invocation="", timer="inactive", timer_enabled=True)]

    watch(cron).check(rig.services)

    assert rig.sent == []
    assert cron.settled == []


def test_a_task_still_running_is_left_until_it_has_ended(rig: Rig) -> None:
    cron = Cron()
    cron.outcomes = [once_outcome(running=True)]

    watch(cron).check(rig.services)

    assert cron.settled == [] and rig.sent == []


def test_a_task_its_own_hook_already_retired_is_not_announced_again(rig: Rig) -> None:
    cron = Cron()
    cron.gone = True
    cron.outcomes = [once_outcome()]

    watch(cron).check(rig.services)

    assert rig.sent == []


def test_a_task_that_repeats_is_still_judged_by_its_timer(rig: Rig) -> None:
    cron = Cron()
    cron.outcomes = [once_outcome(once=False, timer="failed")]

    watch(cron).check(rig.services)

    assert [note.code for note in rig.sent] == ["cron.timer_stopped"]
    assert cron.settled == []
