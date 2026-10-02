# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``app_unreachable`` and ``app_recovered``: an application that stops answering (owner item 58).

In production an application stopped answering with its unit active and no
notification reached Telegram, because the only application event was
``unit_failed``, which is about systemd giving a unit up. These tests pin the
two events (what they say, on which switch, in which language) and the
monitor's rule for sending them: three failed probes spanning a minute, once
per outage, closed by the first success, never while a deploy or a job of that
application runs and never for an application stopped on purpose.
"""

# ruff: noqa: F811

from __future__ import annotations

import http.client
import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import app_lock, lock_path
from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.exceptions import NoustError
from noust.core.notifications.composers import compose_app_recovered, compose_app_unreachable
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import EVENT_KINDS, Notification, State
from noust.core.notifications.render import telegram
from noust.core.runner import FakeRunner
from noust.core.store import App, JobRecord, NoustStore, StoreError
from noust.deployers.docker_compose import HeadlessStackState
from noust.managers.service_manager import ServiceManager
from noust.monitor.process_monitor import MonitorConfig, ProcessMonitor
from noust.monitor.reachability import (
    AppProbe,
    Outage,
    OutageTracker,
    Recovery,
    busy_reason,
    probe_application,
)
from tests.test_monitor_units import FakeEventNotifier, show_block
from tests.test_notifier import config  # noqa: F401  (pytest resolves fixtures by name)

CHAT_ID = "-1001234567890"
PROBE = "GET http://127.0.0.1:3004/ -> <urlopen error [Errno 111] Connection refused>"
SINCE = datetime(2026, 10, 2, 14, 5, tzinfo=timezone.utc)


@pytest.fixture
def ctx() -> NotificationContext:
    return NotificationContext(
        locale="en", server="web-1", public_url="https://console.example.com"
    )


def facts(notification: Notification) -> dict[str, str]:
    return {fact.key: fact.value for fact in notification.facts}


class TestTheEvents:
    def test_both_are_switches_that_ship_on(self) -> None:
        events = DEFAULT_CONFIG["notifications"]["events"]

        assert {"app_unreachable", "app_recovered"} <= set(EVENT_KINDS)
        assert events["app_unreachable"] is True
        assert events["app_recovered"] is True

    def test_unreachable_names_the_domain_since_when_and_the_last_probe_verbatim(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_app_unreachable("shop.example.com", ctx, since=SINCE, probe=PROBE)

        assert (notification.kind, notification.code) == ("app_unreachable", "app.unreachable")
        assert notification.state is State.FAILED
        assert notification.subject == "shop.example.com"
        assert notification.domain == "shop.example.com"
        assert facts(notification)["since"] == "2026-10-02 14:05 UTC"
        assert notification.excerpt is not None
        assert notification.excerpt.lines == (PROBE,)
        assert notification.command is not None
        assert notification.command.value == "noust diagnose shop.example.com"
        assert [link.url for link in notification.links] == [
            "https://console.example.com/apps/shop.example.com"
        ]

    def test_a_worker_is_judged_by_its_containers_and_says_so_line_by_line(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_app_unreachable(
            "licitaciones.example.com",
            ctx,
            since=SINCE,
            probe="worker-1: exited with code 1\nworker-2: restarting (last exit code 1)",
        )

        assert notification.excerpt is not None
        assert notification.excerpt.lines == (
            "worker-1: exited with code 1",
            "worker-2: restarting (last exit code 1)",
        )

    def test_recovered_says_for_how_long_it_did_not_answer(self, ctx: NotificationContext) -> None:
        notification = compose_app_recovered("shop.example.com", ctx, down_for_s=754)

        assert (notification.kind, notification.code) == ("app_recovered", "app.recovered")
        assert notification.state is State.OK
        assert notification.subject == "shop.example.com"
        assert facts(notification)["downtime"] == "12 min 34 s"

    def test_the_words_are_translated_and_the_probe_is_not(self) -> None:
        es = NotificationContext(locale="es", server="web-1")

        notification = compose_app_unreachable("shop.example.com", es, since=SINCE, probe=PROBE)

        assert notification.title == "Aplicación sin respuesta"
        assert notification.excerpt is not None
        assert notification.excerpt.lines == (PROBE,)
        assert (
            compose_app_recovered("shop.example.com", es).title == "Aplicación de nuevo operativa"
        )


class TestOnTelegram:
    def test_the_message_says_the_domain_since_when_and_the_probe_literally(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_app_unreachable("shop.example.com", ctx, since=SINCE, probe=PROBE)

        text = telegram.render(notification, CHAT_ID)["text"]

        assert "shop.example.com" in text
        assert "2026-10-02 14:05 UTC" in text
        # The probe is HTML-escaped for Telegram and otherwise untouched.
        assert "<pre>GET http://127.0.0.1:3004/ -&gt; &lt;urlopen error [Errno 111] " in text


# ---------------------------------------------------------------------------
# When an outage begins and ends
# ---------------------------------------------------------------------------


class TestTheOutageRule:
    """Three failures in a row spanning a minute, once per outage, over at the first success."""

    def test_two_failures_do_not_announce_and_the_third_over_a_minute_does(self) -> None:
        tracker = OutageTracker()

        assert tracker.fail("shop.example.com", PROBE, now=1000.0) is None
        assert tracker.fail("shop.example.com", PROBE, now=1060.0) is None
        outage = tracker.fail("shop.example.com", PROBE, now=1120.0)

        assert outage == Outage("shop.example.com", since=1000.0, probe=PROBE)

    def test_three_failures_inside_a_minute_wait_for_the_minute(self) -> None:
        tracker = OutageTracker()

        for now in (1000.0, 1010.0, 1020.0, 1059.0):
            assert tracker.fail("shop.example.com", PROBE, now=now) is None

        assert tracker.fail("shop.example.com", PROBE, now=1060.0) is not None

    def test_the_outage_carries_the_latest_probe_and_is_announced_once(self) -> None:
        tracker = OutageTracker()
        tracker.fail("shop.example.com", "first", now=1000.0)
        tracker.fail("shop.example.com", "second", now=1060.0)

        outage = tracker.fail("shop.example.com", "third", now=1120.0)

        assert outage is not None
        assert outage.probe == "third"
        assert tracker.fail("shop.example.com", "fourth", now=1180.0) is None
        assert tracker.fail("shop.example.com", "fifth", now=1240.0) is None

    def test_the_first_success_closes_an_announced_outage_and_says_how_long(self) -> None:
        tracker = OutageTracker()
        for now in (1000.0, 1060.0, 1120.0):
            tracker.fail("shop.example.com", PROBE, now=now)

        recovery = tracker.succeed("shop.example.com", now=1754.0)

        assert recovery == Recovery("shop.example.com", down_for_s=754.0)
        assert tracker.succeed("shop.example.com", now=1814.0) is None

    def test_a_second_outage_is_a_second_announcement(self) -> None:
        tracker = OutageTracker()
        for now in (1000.0, 1060.0, 1120.0):
            tracker.fail("shop.example.com", PROBE, now=now)
        tracker.succeed("shop.example.com", now=1180.0)

        for now in (2000.0, 2060.0):
            assert tracker.fail("shop.example.com", PROBE, now=now) is None
        assert tracker.fail("shop.example.com", PROBE, now=2120.0) is not None

    def test_a_success_in_the_middle_starts_the_count_again_and_says_nothing(self) -> None:
        tracker = OutageTracker()
        tracker.fail("shop.example.com", PROBE, now=1000.0)
        tracker.fail("shop.example.com", PROBE, now=1060.0)

        assert tracker.succeed("shop.example.com", now=1120.0) is None
        assert tracker.fail("shop.example.com", PROBE, now=1180.0) is None
        assert tracker.fail("shop.example.com", PROBE, now=1240.0) is None

    def test_applications_are_counted_apart(self) -> None:
        tracker = OutageTracker()
        tracker.fail("a.example.com", PROBE, now=1000.0)
        tracker.fail("b.example.com", PROBE, now=1060.0)

        assert tracker.fail("a.example.com", PROBE, now=1120.0) is None
        assert tracker.fail("b.example.com", PROBE, now=1180.0) is None

    def test_holding_off_for_a_deploy_forgets_a_streak_but_not_an_announced_outage(self) -> None:
        tracker = OutageTracker()
        tracker.fail("a.example.com", PROBE, now=1000.0)
        tracker.fail("a.example.com", PROBE, now=1060.0)
        for now in (1000.0, 1060.0, 1120.0):
            tracker.fail("b.example.com", PROBE, now=now)

        tracker.hold("a.example.com")
        tracker.hold("b.example.com")

        # The deploy cut the run of failures: a needs three new ones.
        assert tracker.fail("a.example.com", PROBE, now=1180.0) is None
        assert tracker.fail("a.example.com", PROBE, now=1240.0) is None
        # b was announced before it, and its first success still closes it.
        assert tracker.succeed("b.example.com", now=1300.0) == Recovery(
            "b.example.com", down_for_s=300.0
        )

    def test_an_application_stopped_on_purpose_leaves_nothing_open(self) -> None:
        tracker = OutageTracker()
        for now in (1000.0, 1060.0, 1120.0):
            tracker.fail("shop.example.com", PROBE, now=now)

        tracker.forget("shop.example.com")

        assert tracker.succeed("shop.example.com", now=9000.0) is None

    def test_a_run_of_failures_the_monitor_was_not_there_to_see_is_not_consecutive(self) -> None:
        tracker = OutageTracker()
        tracker.fail("shop.example.com", PROBE, now=1000.0)
        tracker.fail("shop.example.com", PROBE, now=1060.0)

        # The monitor was stopped for an hour.
        assert tracker.fail("shop.example.com", PROBE, now=4660.0) is None
        assert tracker.fail("shop.example.com", PROBE, now=4720.0) is None
        assert tracker.fail("shop.example.com", PROBE, now=4780.0) is not None

    def test_what_it_knows_survives_a_restart_of_the_monitor(self) -> None:
        tracker = OutageTracker()
        tracker.fail("shop.example.com", PROBE, now=1000.0)
        tracker.fail("shop.example.com", PROBE, now=1060.0)

        revived = OutageTracker.from_dict(json.loads(json.dumps(tracker.to_dict())))

        assert revived.fail("shop.example.com", PROBE, now=1120.0) is not None
        assert revived.succeed("shop.example.com", now=1180.0) is not None

    def test_a_state_file_it_cannot_read_means_no_memory_not_an_error(self) -> None:
        assert OutageTracker.from_dict("nonsense").to_dict() == {}  # type: ignore[arg-type]
        assert OutageTracker.from_dict({"a.example.com": "nonsense"}).to_dict() == {}

    def test_an_application_that_is_gone_is_dropped_silently(self) -> None:
        tracker = OutageTracker()
        for now in (1000.0, 1060.0, 1120.0):
            tracker.fail("gone.example.com", PROBE, now=now)

        tracker.keep_only({"shop.example.com"})

        assert tracker.to_dict() == {}


# ---------------------------------------------------------------------------
# What one scan asks about one application
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store in the test directory; the application locks live beside it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


def make_app(**fields: Any) -> App:
    """An application row as the store would hold it."""
    values: dict[str, Any] = {
        "domain": "shop.example.com",
        "app_type": "nextjs",
        "port": 3004,
        "app_path": "/var/www/apps/shop-example-com",
    }
    values.update(fields)
    return App(**values)


class FakeHttp:
    """
    The health gate's own probe, answering from a script.

    ``answer`` is what each call reports: True for an answer the application's
    expectation accepts, or the text a failed attempt reports.
    """

    def __init__(self, answer: bool | str) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> bool:
        self.calls.append({"url": url, **kwargs})
        if self.answer is True:
            return True
        if isinstance(self.answer, str):
            kwargs["on_attempt"](f"Health check attempt 1 failed: {self.answer}")
        return False


class TestProbingAnApplication:
    def test_it_asks_what_the_health_gate_asks_once(self) -> None:
        http = FakeHttp(True)
        app = make_app(health_path="/healthz", health_expect="200-299")

        result = probe_application(app, http=http)

        assert result == AppProbe(ok=True, detail="", containers=False)
        (call,) = http.calls
        assert call["url"] == "http://127.0.0.1:3004/healthz"
        assert call["retries"] == 1
        # The operator's expectation decides, not "anything below 500".
        assert call["accept"](204) and not call["accept"](301)

    def test_a_failed_probe_is_kept_verbatim_with_what_was_asked(self) -> None:
        http = FakeHttp("<urlopen error [Errno 111] Connection refused>")

        result = probe_application(make_app(), http=http)

        assert result == AppProbe(
            ok=False,
            detail="GET http://127.0.0.1:3004/ -> <urlopen error [Errno 111] Connection refused>",
            containers=False,
        )

    def test_an_application_that_answers_garbage_is_one_that_does_not_answer(self) -> None:
        """urllib lets http.client's own errors through; they are what a broken server says."""

        def garbage(url: str, **kwargs: Any) -> bool:
            raise http.client.BadStatusLine("''")

        result = probe_application(make_app(), http=garbage)

        assert result == AppProbe(
            ok=False, detail="GET http://127.0.0.1:3004/ -> BadStatusLine: ''", containers=False
        )

    def test_a_server_error_is_what_the_probe_reports(self) -> None:
        result = probe_application(make_app(), http=FakeHttp("HTTP Error 502: Bad Gateway"))

        assert result is not None
        assert result.detail == "GET http://127.0.0.1:3004/ -> HTTP Error 502: Bad Gateway"

    def test_a_zero_downtime_application_is_asked_on_the_instance_that_serves(self) -> None:
        http = FakeHttp(True)
        app = make_app(zero_downtime=True, active_color="green")

        probe_application(app, http=http)

        assert http.calls[0]["url"] != "http://127.0.0.1:3004/"

    @pytest.mark.parametrize(
        "fields",
        [
            {"app_type": "static", "is_static": True, "port": None},
            {"app_type": "php-fpm", "is_static": True, "port": None},
            {"app_type": "nodejs", "port": None},
        ],
    )
    def test_what_nothing_listens_for_is_not_probed(self, fields: dict[str, Any]) -> None:
        http = FakeHttp(False)

        assert probe_application(make_app(**fields), http=http) is None
        assert http.calls == []


class TestProbingAStackWithNoWeb:
    """A Compose stack that publishes no port is judged by its containers (item 57)."""

    def stack(self, *, healthy: bool, problems: tuple[str, ...] = ()) -> Any:
        return HeadlessStackState(
            healthy=healthy,
            summary="containers that are not running: " + "; ".join(problems)
            if problems
            else "all 2 of its containers are running or finished cleanly",
            problems=problems,
            output="",
            recorded_port=3000,
        )

    def test_a_container_that_exited_with_an_error_is_an_outage_line_by_line(self) -> None:
        http = FakeHttp(True)
        state = self.stack(
            healthy=False,
            problems=("worker-1: exited with code 1", "worker-2: restarting (last exit code 1)"),
        )

        result = probe_application(
            make_app(app_type="docker-compose", port=3000), http=http, stack_state=lambda app: state
        )

        assert result == AppProbe(
            ok=False,
            detail="worker-1: exited with code 1\nworker-2: restarting (last exit code 1)",
            containers=True,
        )
        # The port 1.x recorded for it answers nothing and is never asked.
        assert http.calls == []

    def test_a_worker_whose_containers_run_is_fine(self) -> None:
        result = probe_application(
            make_app(app_type="docker-compose", port=None),
            http=FakeHttp(False),
            stack_state=lambda app: self.stack(healthy=True),
        )

        assert result == AppProbe(ok=True, detail="", containers=True)

    def test_docker_that_cannot_list_the_containers_says_so_verbatim(self) -> None:
        state = HeadlessStackState(
            False,
            "docker compose could not list its containers",
            (),
            "Cannot connect to the Docker daemon",
            None,
        )

        result = probe_application(
            make_app(app_type="docker-compose", port=None),
            http=FakeHttp(True),
            stack_state=lambda app: state,
        )

        assert result is not None and not result.ok
        assert "docker compose could not list its containers" in result.detail
        assert "Cannot connect to the Docker daemon" in result.detail

    def test_a_stack_that_publishes_a_port_is_probed_like_any_web(self) -> None:
        http = FakeHttp(True)

        result = probe_application(
            make_app(app_type="docker-compose", port=3000), http=http, stack_state=lambda app: None
        )

        assert result == AppProbe(ok=True, detail="", containers=False)
        assert http.calls[0]["url"] == "http://127.0.0.1:3000/"


class TestWhenSomethingIsChangingTheApplication:
    NOW = 1_800_000_000.0

    def test_a_deploy_holding_the_lock_makes_it_busy_and_says_what(self, store: NoustStore) -> None:
        with app_lock("shop.example.com", "update"):
            reason = busy_reason("shop.example.com", store, now=time.time())

        assert reason is not None and reason.startswith("update")

    def test_once_it_ends_the_application_is_not_busy(self, store: NoustStore) -> None:
        with app_lock("shop.example.com", "update"):
            pass

        assert busy_reason("shop.example.com", store, now=time.time()) is None

    def test_another_applications_lock_is_not_this_ones(self, store: NoustStore) -> None:
        with app_lock("other.example.com", "update"):
            assert busy_reason("shop.example.com", store, now=time.time()) is None

    def test_a_record_left_by_a_process_that_died_is_not_believed(self, store: NoustStore) -> None:
        write_lock_record(store, pid=2**31 - 2, started_at=datetime.now(timezone.utc))

        assert busy_reason("shop.example.com", store, now=time.time()) is None

    def test_a_record_older_than_any_deploy_is_not_believed(self, store: NoustStore) -> None:
        # Our own pid is alive, so only its age says the record is a leftover.
        started = datetime.now(timezone.utc) - timedelta(hours=7)
        write_lock_record(store, pid=os.getpid(), started_at=started)

        assert busy_reason("shop.example.com", store, now=time.time()) is None

    def test_a_name_that_could_not_be_an_application_has_nothing_running(
        self, store: NoustStore
    ) -> None:
        assert busy_reason("../etc/passwd", store, now=time.time()) is None

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_a_job_of_the_application_makes_it_busy(self, store: NoustStore, status: str) -> None:
        store.create_job(job("j1", domain="shop.example.com", status=status))

        reason = busy_reason("shop.example.com", store, now=time.time())

        assert reason is not None and "restart" in reason

    def test_a_job_of_another_application_or_one_that_ended_does_not(
        self, store: NoustStore
    ) -> None:
        store.create_job(job("j1", domain="other.example.com", status="running"))
        store.create_job(job("j2", domain="shop.example.com", status="completed"))
        store.create_job(job("j3", domain="shop.example.com", status="failed"))

        assert busy_reason("shop.example.com", store, now=time.time()) is None

    def test_a_job_row_nobody_closed_is_not_believed_forever(self, store: NoustStore) -> None:
        old = (datetime.now() - timedelta(hours=7)).isoformat()
        store.create_job(
            job("j1", domain="shop.example.com", status="running", created_at=old, started_at=old)
        )

        assert busy_reason("shop.example.com", store, now=time.time()) is None


def job(job_id: str, **fields: Any) -> JobRecord:
    """A job row, running a restart unless told otherwise."""
    values: dict[str, Any] = {
        "id": job_id,
        "type": "restart",
        "name": "Restart shop.example.com",
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "started_at": datetime.now().isoformat(),
    }
    values.update(fields)
    return JobRecord(**values)


def write_lock_record(store: NoustStore, *, pid: int, started_at: datetime) -> None:
    """Leave an application's lock file as a holder that never cleaned up would."""
    path = lock_path("shop.example.com")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "operation": "update",
                "pid": pid,
                "started_at": started_at.isoformat(timespec="seconds"),
            }
        )
    )


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------

DOMAIN = "shop.example.com"
UNIT = "shop-example-com"
RUNNING = show_block(f"{UNIT}.service")
STOPPED_BY_COMMAND = show_block(f"{UNIT}.service", active="inactive", sub="dead", result="success")
CRASHED = show_block(f"{UNIT}.service", active="failed", sub="failed", result="exit-code", status=1)


class FakeUnits:
    """Which units serve each application, with the real unit reader on a fake runner."""

    def __init__(self, runner: FakeRunner, units: dict[str, list[str]]) -> None:
        self.units = units
        self.describe_units = ServiceManager(runner=runner).describe_units

    def serving_units(self, app: App) -> list[str]:
        return self.units.get(app.domain, [])

    def managed_units(self) -> list[Any]:
        return []

    def logs(self, name: str, lines: int = 50) -> str:
        return ""


class Prober:
    """What the monitor's probe finds, set by the test; every call is kept."""

    def __init__(self) -> None:
        self.result: AppProbe | Exception | None = AppProbe(ok=True, detail="")
        self.calls: list[str] = []

    def __call__(self, app: App) -> AppProbe | None:
        self.calls.append(app.domain)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def down(self, detail: str = PROBE, *, containers: bool = False) -> None:
        self.result = AppProbe(ok=False, detail=detail, containers=containers)

    def up(self, *, containers: bool = False) -> None:
        self.result = AppProbe(ok=True, detail="", containers=containers)


class Scans:
    """A monitor over fakes, and a clock the test moves one scan at a time."""

    def __init__(self, tmp_path: Path, store: NoustStore, **app_fields: Any) -> None:
        self.runner = FakeRunner()
        self.prober = Prober()
        self.notifier = FakeEventNotifier()
        # The locks and jobs the tests make are stamped with the real clock.
        self.start = time.time()
        self.now = self.start
        self.state_path = tmp_path / "reachability.json"
        self.store = store
        self.tmp_path = tmp_path
        store.create_app(make_app(**app_fields))
        self.monitor = self.build()

    def build(self) -> ProcessMonitor:
        monitor = ProcessMonitor(
            config=MonitorConfig(),
            runner=self.runner,
            event_notifier=self.notifier,
            service_manager=FakeUnits(self.runner, {DOMAIN: [UNIT]}),
            reachability_probe=self.prober,
            reachability_state_path=self.state_path,
            clock=lambda: self.now,
        )
        monitor.global_config.reload = lambda: None  # type: ignore[method-assign]
        return monitor

    def scan(self, unit: str = RUNNING, *, after: float = 60.0) -> None:
        """One scan, ``after`` seconds after the last, with systemd answering ``unit``."""
        self.now += after
        self.runner.script(["systemctl", "show"], stdout=unit)
        self.monitor._check_reachability()

    @property
    def codes(self) -> list[str]:
        return [event.code for event in self.notifier.events]


@pytest.fixture
def scans(tmp_path: Path, store: NoustStore) -> Scans:
    return Scans(tmp_path, store)


class TestTheMonitorAnnouncesAnOutage:
    def test_two_failed_probes_say_nothing_and_the_third_a_minute_later_says_it_once(
        self, scans: Scans
    ) -> None:
        scans.prober.down()

        scans.scan()
        scans.scan()
        assert scans.codes == []

        scans.scan()
        scans.scan()
        scans.scan()

        assert scans.codes == ["app.unreachable"]

    def test_what_it_says_is_the_domain_since_when_and_the_last_probe_verbatim(
        self, scans: Scans
    ) -> None:
        scans.prober.down()
        for _ in range(3):
            scans.scan()

        (event,) = scans.notifier.events

        assert (event.kind, event.state) == ("app_unreachable", State.FAILED)
        assert event.subject == DOMAIN
        assert event.domain == DOMAIN
        first_failure = datetime.fromtimestamp(scans.start + 60.0, tz=timezone.utc)
        assert {fact.key: fact.value for fact in event.facts}["since"] == (
            f"{first_failure:%Y-%m-%d %H:%M} UTC"
        )
        assert event.excerpt is not None and event.excerpt.lines == (PROBE,)

    def test_the_first_answer_after_it_closes_the_alert_with_how_long_it_lasted(
        self, scans: Scans
    ) -> None:
        scans.prober.down()
        for _ in range(3):
            scans.scan()
        scans.prober.up()

        scans.scan()
        scans.scan()

        assert scans.codes == ["app.unreachable", "app.recovered"]
        recovered = scans.notifier.events[1]
        assert recovered.kind == "app_recovered"
        assert {fact.key: fact.value for fact in recovered.facts}["downtime"] == "3 min"

    def test_an_application_that_answers_all_along_says_nothing(self, scans: Scans) -> None:
        for _ in range(5):
            scans.scan()

        assert scans.codes == []
        assert scans.prober.calls == [DOMAIN] * 5

    def test_a_blip_between_answers_is_not_an_outage(self, scans: Scans) -> None:
        scans.prober.down()
        scans.scan()
        scans.scan()
        scans.prober.up()
        scans.scan()
        scans.prober.down()
        scans.scan()
        scans.scan()

        assert scans.codes == []

    def test_a_stack_with_no_web_is_announced_by_its_containers_in_both_directions(
        self, scans: Scans
    ) -> None:
        scans.prober.down("worker-1: exited with code 1", containers=True)
        for _ in range(3):
            scans.scan()
        scans.prober.up(containers=True)
        scans.scan()

        assert scans.codes == ["app.unreachable", "app.recovered"]
        unreachable, recovered = scans.notifier.events
        assert "container" in unreachable.summary
        assert unreachable.excerpt is not None
        assert unreachable.excerpt.lines == ("worker-1: exited with code 1",)
        assert "container" in recovered.summary

    def test_a_probe_that_could_not_be_made_counts_for_nothing(self, scans: Scans) -> None:
        scans.prober.result = NoustError("The compose file is unreadable")

        for _ in range(5):
            scans.scan()

        assert scans.codes == []

    def test_not_knowing_whether_something_is_changing_it_asks_nothing_and_stops_nothing(
        self, scans: Scans, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unreadable(domain: str, store: NoustStore, *, now: float) -> str | None:
            raise StoreError("database is locked")

        monkeypatch.setattr("noust.monitor.process_monitor.busy_reason", unreadable)
        scans.prober.down()

        for _ in range(5):
            scans.scan()

        assert scans.prober.calls == []
        assert scans.codes == []

    def test_what_nothing_can_be_asked_of_is_left_alone(self, scans: Scans) -> None:
        scans.prober.result = None

        for _ in range(5):
            scans.scan()

        assert scans.codes == []


class TestTheMonitorStaysQuietWhenTheOutageIsNotTheApplications:
    def test_nothing_is_asked_while_a_deploy_runs(self, scans: Scans) -> None:
        scans.prober.down()

        with app_lock(DOMAIN, "update"):
            for _ in range(5):
                scans.scan()

        assert scans.prober.calls == []
        assert scans.codes == []

    def test_the_failures_before_a_deploy_do_not_count_after_it(self, scans: Scans) -> None:
        scans.prober.down()
        scans.scan()
        scans.scan()
        with app_lock(DOMAIN, "update"):
            scans.scan()
        scans.scan()
        scans.scan()

        assert scans.codes == []

    def test_nothing_is_asked_while_a_job_of_the_application_runs(self, scans: Scans) -> None:
        scans.store.create_job(job("j1", domain=DOMAIN, status="running"))
        scans.prober.down()

        for _ in range(5):
            scans.scan()

        assert scans.prober.calls == []
        assert scans.codes == []

    def test_an_application_stopped_by_its_operator_is_not_asked_and_not_announced(
        self, scans: Scans
    ) -> None:
        scans.prober.down()

        for _ in range(5):
            scans.scan(STOPPED_BY_COMMAND)

        assert scans.prober.calls == []
        assert scans.codes == []

    def test_stopping_it_after_an_alert_closes_nothing_and_starting_it_does_not_recover(
        self, scans: Scans
    ) -> None:
        scans.prober.down()
        for _ in range(3):
            scans.scan()
        scans.scan(STOPPED_BY_COMMAND)
        scans.prober.up()
        scans.scan()

        assert scans.codes == ["app.unreachable"]

    def test_a_unit_that_failed_is_unit_faileds_to_tell_not_this_ones(self, scans: Scans) -> None:
        scans.prober.down()

        for _ in range(5):
            scans.scan(CRASHED)

        assert scans.prober.calls == []
        assert scans.codes == []

    def test_the_end_of_an_outage_is_told_even_if_the_unit_failed_in_between(
        self, scans: Scans
    ) -> None:
        scans.prober.down()
        for _ in range(3):
            scans.scan()
        scans.scan(CRASHED)
        scans.prober.up()
        scans.scan()

        assert scans.codes == ["app.unreachable", "app.recovered"]

    def test_a_site_with_no_unit_is_not_asked(self, tmp_path: Path, store: NoustStore) -> None:
        scans = Scans(tmp_path, store)
        scans.monitor.service_manager.units = {}  # type: ignore[attr-defined]
        scans.prober.down()

        for _ in range(5):
            scans.scan()

        assert scans.prober.calls == []


class TestTheMonitorRemembersAcrossScans:
    def test_a_restart_of_the_daemon_neither_loses_the_run_nor_repeats_the_alert(
        self, scans: Scans
    ) -> None:
        scans.prober.down()
        scans.scan()
        scans.scan()

        revived = scans.build()
        scans.monitor = revived
        scans.scan()
        assert scans.codes == ["app.unreachable"]

        scans.monitor = scans.build()
        scans.scan()
        scans.prober.up()
        scans.scan()
        assert scans.codes == ["app.unreachable", "app.recovered"]

    def test_the_state_is_written_through_the_filesystem_seam_and_kept_private(
        self, scans: Scans
    ) -> None:
        scans.prober.down()
        scans.scan()

        state = json.loads(scans.state_path.read_text())

        assert state[DOMAIN]["failures"] == 1
        assert state[DOMAIN]["probe"] == PROBE
        assert scans.state_path.stat().st_mode & 0o777 == 0o600

    def test_a_state_file_that_is_garbage_starts_the_count_afresh(self, scans: Scans) -> None:
        scans.state_path.write_text("{not json")
        scans.prober.down()

        for _ in range(3):
            scans.scan()

        assert scans.codes == ["app.unreachable"]

    def test_an_application_deleted_meanwhile_is_forgotten(self, scans: Scans) -> None:
        scans.prober.down()
        scans.scan()
        scans.store.delete_app(DOMAIN)

        scans.scan()

        assert json.loads(scans.state_path.read_text()) == {}

    def test_the_language_of_the_announcement_is_the_configured_one(
        self,
        config: Config,
        scans: Scans,
    ) -> None:
        config.set("notifications.language", "es")
        scans.prober.down()

        for _ in range(3):
            scans.scan()

        assert scans.notifier.events[0].title == "Aplicación sin respuesta"


def test_the_daemons_loop_checks_reachability_every_round(
    scans: Scans, monkeypatch: pytest.MonkeyPatch
) -> None:
    monitor = scans.monitor
    rounds: list[str] = []

    def check() -> None:
        rounds.append("checked")
        monitor.stop()

    class Idle:
        def stop(self) -> None:
            pass

    for name in (
        "_start_metrics",
        "_stop_metrics",
        "_log_metrics",
        "_report_services",
        "_check_certificates",
        "_refresh_security_report",
        "scan_once",
        "_purge_old_observations",
    ):
        monkeypatch.setattr(monitor, name, lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_check_reachability", check)
    monkeypatch.setattr("noust.deployers.helpers.sandbox.sweep_at_start", lambda: None)
    monkeypatch.setattr("noust.fleet.probe.start_probe", lambda daemon=True: Idle())

    monitor.run()

    assert rounds == ["checked"]
