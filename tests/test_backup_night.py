# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A night of bad network is one message, or none (item 201).

In a real fleet the network between a server and its backup destination lost packets for hours.
That server took fifteen backups and nine could not be sent: each send had one attempt, nothing sent
again what did not arrive, every failure was said twice (the backup's own notice and the monitor's
``unit_failed`` for the unit that ran it), one notice per application, and the destination watch said
the destination was unreachable after a single failed probe. Nine failures were eighteen messages and
a nineteenth.

This is that night, run through the real runs, the real monitor check and the real destination
watch, over a fake rclone whose network is a switch and a clock that only the test moves. The three
tests are the exit criterion of the work: what goes out today, what goes out when the network comes
back before the first retry gives up, and what goes out when it does not.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.exceptions import BackupError
from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import OFF_BY_DEFAULT, Notification
from noust.core.pending_sends import PendingSends
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore, get_store
from noust.managers.backup_resend import Resender
from noust.managers.backup_scheduler import run_schedule
from noust.monitor.watch_destinations import DestinationWatch
from noust.monitor.watches import WatchServices
from tests.support.backup_night import APPS, FAILING_RUNS, Night, app_name, make_night
from tests.support.monitor_units import make_monitor, scan, show_block
from tests.support.notifier import config  # noqa: F401  (pytest resolves fixtures by name)

# ruff: noqa: F811


@pytest.fixture(autouse=True)
def _fresh_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[Notification]:
    """Every notification any code of the night handed to the notifier."""
    heard: list[Notification] = []
    monkeypatch.setattr(
        "noust.core.notifier.Notifier.notify", lambda self, event: heard.append(event)
    )
    return heard


@pytest.fixture
def night(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: Config) -> Night:
    built = make_night(tmp_path / "remote", monkeypatch, tmp_path)
    assert isinstance(built, Night)
    return built


def unit_of(domain: str) -> str:
    """The unit the timer of an application's backup runs."""
    return f"noust-backup-{app_name(domain)}"


def the_fifteen_runs(night: Night, *, down_for: frozenset[int] = FAILING_RUNS) -> list[str]:
    """
    Run the fifteen scheduled backups, twenty minutes apart, with the network bad for some.

    Args:
        night: The night.
        down_for: The runs the network is bad during.

    Returns:
        The units whose run ended in an error, which is what systemd calls failed.
    """
    failed: list[str] = []
    for index, domain in enumerate(APPS):
        night.move_to(night.start.replace(hour=1) + _minutes(20 * index))
        night.runner.down = index in down_for
        try:
            run_schedule(domain, runner=night.runner)
        except BackupError:
            failed.append(unit_of(domain))
    night.runner.down = False
    return failed


def _minutes(count: int):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return timedelta(minutes=count)


def what_the_monitor_said(failed_units: list[str]) -> list[Notification]:
    """
    Let the monitor look at the units, the way it does every minute.

    Args:
        failed_units: The units systemd reports as failed.

    Returns:
        What the monitor announced.
    """
    runner = FakeRunner()
    monitor, announced = make_monitor(runner, [unit_of(domain) for domain in APPS])
    blocks = [
        show_block(
            f"{unit_of(domain)}.service",
            active="failed" if unit_of(domain) in failed_units else "inactive",
            sub="failed" if unit_of(domain) in failed_units else "dead",
            result="exit-code" if unit_of(domain) in failed_units else "success",
            status=1 if unit_of(domain) in failed_units else 0,
        )
        for domain in APPS
    ]
    scan(runner, monitor, *blocks)
    return list(announced.events)


class WatchRig:
    """The destination watch over the night's clock, remembering in the store like the monitor."""

    def __init__(self, night: Night, config: Config) -> None:
        self.night = night
        self.sent: list[Notification] = []
        self.answering = False
        self.asked = 0
        self.watch = DestinationWatch(self._ask, in_use=lambda: {"nas"})
        self.services = WatchServices(
            publish=lambda build: self.sent.append(build(NotificationContext(server="web-1"))),
            state=NotificationState(get_store(), clock=night.epoch),
            runner=FakeRunner(),
            config=config,
            clock=night.epoch,
        )

    def _ask(self, name: str) -> object:
        self.asked += 1
        if not self.answering:
            raise BackupError(f"Could not reach backup destination {name!r}", details="timeout")
        return {"ok": True}

    def probe(self) -> None:
        """One look of the watch."""
        self.watch.check(self.services)


def audible(events: list[Notification]) -> list[Notification]:
    """
    The notifications an operator hears.

    The heartbeat of a backup that went well ships switched off; the notifier drops it, and so does
    this (the test replaces the notifier, which is where that switch is read).
    """
    return [event for event in events if event.kind not in OFF_BY_DEFAULT]


def counted(events: list[Notification]) -> dict[str, int]:
    """How many of each notification code the operator hears."""
    found: dict[str, int] = {}
    for event in audible(events):
        found[event.code] = found.get(event.code, 0) + 1
    return found


def test_the_night_costs_nineteen_messages_without_item_201_and_none_when_the_retry_arrives(
    night: Night, notified: list[Notification], config: Config
) -> None:
    """
    The exit criterion, first case: the network is back before the first retry gives up.

    Before this work nine failed sends were nineteen messages (nine notices of the upload, nine of
    the monitor for the nine units that ended in an error, and one for the destination after a single
    failed probe) and nine copies that were nowhere else. Now no run fails, nothing is said, and the
    nine copies that did not arrive are sent by themselves a quarter of an hour later.
    """
    failed_units = the_fifteen_runs(night)
    watch = WatchRig(night, config)
    watch.probe()
    night.runner.down = False

    # The next look of the timer, the first retry, finds the destination answering.
    night.advance(hours=3, minutes=10)
    report = Resender(runner=night.runner).run()

    said = notified + what_the_monitor_said(failed_units) + watch.sent
    assert failed_units == []
    assert report.sent == len(FAILING_RUNS) and report.failed == 0
    assert audible(said) == []
    assert PendingSends().all() == []
    for index in FAILING_RUNS:
        domain = APPS[index]
        assert night.remote_has(domain, night.backups[domain].id)


def test_the_night_costs_one_message_and_its_recovery_when_the_retry_fails_too(
    night: Night, notified: list[Notification], config: Config
) -> None:
    """
    The exit criterion, second case: the first retry fails as well.

    One message for the destination, which names the nine copies and the reason, and one when they
    have all arrived, which answers it. The monitor says nothing about the units (no run failed) and
    the destination watch, which fails three probes, finds the destination already spoken for.
    """
    the_fifteen_runs(night)
    night.runner.down = True
    night.advance(hours=3, minutes=10)
    Resender(runner=night.runner).run()
    said_after_the_retry = [note.code for note in audible(notified)]
    watch = WatchRig(night, config)
    for _ in range(3):
        watch.probe()
        night.advance(minutes=5)
    assert said_after_the_retry == ["backup.sends_failed"]
    failed = audible(notified)[0]
    copies = next(fact for fact in failed.facts if fact.key == "copies")
    assert copies.value.startswith(str(len(FAILING_RUNS)))
    assert failed.excerpt is not None and "i/o timeout" in " ".join(failed.excerpt.lines)

    night.runner.down = False
    night.advance(hours=2)
    Resender(runner=night.runner).run()
    watch.answering = True
    watch.probe()

    said = notified + what_the_monitor_said([]) + watch.sent
    assert [note.code for note in audible(said)] == [
        "backup.sends_failed",
        "backup.sends_recovered",
    ]
    assert PendingSends().all() == []
    assert grouping.thing_key(audible(said)[0]) == grouping.thing_key(audible(said)[1])
    recovered = audible(said)[1]
    assert next(fact for fact in recovered.facts if fact.key == "copies").value == str(
        len(FAILING_RUNS)
    )


def test_when_the_watch_spoke_first_the_runs_do_not_say_it_again(
    night: Night, notified: list[Notification], config: Config
) -> None:
    """The destination was found down before any run failed: one message says it, and when it is back."""
    watch = WatchRig(night, config)
    for _ in range(3):
        watch.probe()
        night.advance(minutes=5)
    assert [note.code for note in watch.sent] == ["destination.unreachable"]

    the_fifteen_runs(night)
    night.runner.down = True
    night.advance(hours=3, minutes=10)
    Resender(runner=night.runner).run()
    night.runner.down = False
    night.advance(hours=2)
    Resender(runner=night.runner).run()
    watch.answering = True
    watch.probe()

    said = notified + watch.sent
    assert [note.code for note in audible(said)] == [
        "destination.unreachable",
        "destination.recovered",
    ]
