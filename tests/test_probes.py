# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Probes of an address, from outside the application (checklist G-17).

What is defended: a probe is checked before it is kept (no credential in an
address, no word to look for in a HEAD), an ask judges the status and the word
and never raises for a bad answer, an address on the machine's own network is
refused as a notification's is, *down* needs several bad answers in a row and is
told once, an answer that comes back is told once, and every stretch down is an
incident that ends.
"""

from __future__ import annotations

import io
import socket
import ssl
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.exceptions import ValidationError
from noust.core.notification_state import NotificationState
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.probe_store import (
    DAILY_KEEP_DAYS,
    MAX_PROBES,
    RAW_KEEP_DAYS,
    Probe,
    ProbeState,
    ProbeStore,
    validate_probe,
)
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.probes import (
    Answer,
    ProbeEngine,
    ProbeManager,
    ask,
    describe_error,
    public_address,
)
from noust.monitor.watch_probes import ProbeWatch
from noust.monitor.watches import WatchServices

DAY = 86_400
NOW = 1_800_000_000.0
URL = "https://shop.example.com/health"


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def probes(store: NoustStore, clock: Clock) -> ProbeStore:
    return ProbeStore(store, clock=clock)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


def probe(**changes: Any) -> Probe:
    values: dict[str, Any] = {"name": "shop", "url": URL}
    values.update(changes)
    return Probe(**values)


class TestAProbeIsCheckedBeforeItIsKept:
    def test_a_plain_probe_is_accepted_and_normalised(self) -> None:
        checked = validate_probe(probe(method="get", expect_status=" 200 - 399".replace(" ", "")))

        assert (checked.method, checked.expect_status) == ("GET", "200-399")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", "Shop"),
            ("name", "1shop"),
            ("name", ""),
            ("url", "ftp://example.com"),
            ("url", "example.com/health"),
            ("url", "https://user:secret@example.com/"),
            ("url", "https://example.com/a b"),
            ("url", "https:///nohost"),
            ("method", "POST"),
            ("expect_status", "ok"),
            ("expect_status", "200-"),
            ("expect_status", "99"),
            ("expect_status", "600"),
            ("expect_status", "399-200"),
            ("interval_s", 5),
            ("interval_s", 100_000),
            ("timeout_s", 0),
            ("fail_after", 0),
            ("fail_after", 11),
            ("title", "x" * 81),
            ("expect_text", "x" * 201),
        ],
    )
    def test_what_is_not_acceptable_names_its_field(self, field: str, value: Any) -> None:
        with pytest.raises(ValidationError) as raised:
            validate_probe(probe(**{field: value}))

        assert raised.value.field == field

    def test_an_answer_may_not_take_longer_than_the_time_between_asks(self) -> None:
        with pytest.raises(ValidationError) as raised:
            validate_probe(probe(interval_s=30, timeout_s=30))

        assert raised.value.field == "timeout_s"

    def test_a_word_cannot_be_looked_for_in_an_answer_with_no_body(self) -> None:
        with pytest.raises(ValidationError) as raised:
            validate_probe(probe(method="HEAD", expect_text="ok"))

        assert raised.value.field == "expect_text"

    def test_the_statuses_that_mean_well(self) -> None:
        wanted = validate_probe(probe(expect_status="200,204,300-399"))

        assert [wanted.accepts(code) for code in (200, 204, 301, 399, 400, 500)] == [
            True,
            True,
            True,
            True,
            False,
            False,
        ]


class TestTheStore:
    def test_a_probe_is_created_replaced_and_removed(self, probes: ProbeStore) -> None:
        created = probes.save(probe(title="Shop"), actor="ana")
        assert created.id is not None and created.title == "Shop"

        replaced = probes.save(probe(title="The shop", interval_s=120), actor="ana")
        assert replaced.id == created.id and replaced.interval_s == 120
        assert [p.name for p in probes.probes()] == ["shop"]

        assert probes.remove("shop") is True
        assert probes.remove("shop") is False
        assert probes.get("shop") is None

    def test_there_is_a_limit_to_how_many(self, probes: ProbeStore) -> None:
        for number in range(MAX_PROBES):
            probes.save(probe(name=f"p{number}"))

        with pytest.raises(ValidationError, match="at most"):
            probes.save(probe(name="one-more"))

    def test_changing_the_address_forgets_what_the_old_one_had_decided(
        self, probes: ProbeStore
    ) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        probes.put_state(ProbeState(saved.id, "down", NOW - 600, failures=3, announced=True))
        probes.open_incident(saved.id, NOW - 600, "HTTP 503")

        probes.save(probe(url="https://shop.example.com/other"))

        assert probes.state(saved.id).state == "unknown"
        (incident,) = probes.incidents(saved.id)
        assert incident.ended_at is not None

    def test_switching_a_probe_off_ends_its_incident(self, probes: ProbeStore) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        probes.open_incident(saved.id, NOW - 600, "timed out")

        probes.set_enabled("shop", False)

        assert probes.incidents(saved.id)[0].ended_at is not None
        assert probes.set_enabled("nope", True) is None

    def test_removing_a_probe_removes_what_it_kept(
        self, probes: ProbeStore, store: NoustStore
    ) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        probes.record_result(saved.id, ok=True, status=200, latency_ms=10, error=None)
        probes.put_state(ProbeState(saved.id, "up", NOW))
        probes.open_incident(saved.id, NOW, "x")

        probes.remove("shop")

        with store._transaction() as cursor:
            for count in (
                "SELECT COUNT(*) FROM probe_state",
                "SELECT COUNT(*) FROM probe_results",
                "SELECT COUNT(*) FROM probe_daily",
                "SELECT COUNT(*) FROM probe_incidents",
            ):
                cursor.execute(count)
                assert cursor.fetchone()[0] == 0, count

    def test_uptime_and_latency_come_from_the_days(self, probes: ProbeStore, clock: Clock) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        for ok, latency in ((True, 100), (True, 300), (False, None), (True, 200)):
            probes.record_result(
                saved.id, ok=ok, status=200 if ok else 503, latency_ms=latency, error=None
            )

        uptime = probes.uptime(saved.id, 1)

        assert (uptime.checks, uptime.good) == (4, 3)
        assert uptime.percent == 75.0
        assert uptime.mean_ms == 200.0 and uptime.max_ms == 300

    def test_a_probe_never_asked_has_no_percentage(self, probes: ProbeStore) -> None:
        saved = probes.save(probe())
        assert saved.id is not None

        assert probes.uptime(saved.id, 30).percent is None

    def test_the_days_are_every_day_of_the_window_oldest_first(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        clock.now = NOW - 2 * DAY
        probes.record_result(saved.id, ok=True, status=200, latency_ms=5, error=None)
        clock.now = NOW

        bars = probes.days(saved.id, 4)

        assert len(bars) == 4
        assert [bar.day for bar in bars] == sorted(bar.day for bar in bars)
        assert [bar.checks for bar in bars] == [0, 1, 0, 0]

    def test_recent_answers_are_returned_oldest_first(self, probes: ProbeStore) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        for index in range(5):
            probes.record_result(
                saved.id, ok=True, status=200, latency_ms=index, error=None, at=NOW + index
            )

        assert [item["latency_ms"] for item in probes.recent(saved.id, limit=3)] == [2, 3, 4]

    def test_retention_keeps_the_days_longer_than_the_answers(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        clock.now = NOW - (RAW_KEEP_DAYS + 2) * DAY
        probes.record_result(saved.id, ok=True, status=200, latency_ms=5, error=None)
        clock.now = NOW - (DAILY_KEEP_DAYS + 2) * DAY
        probes.record_result(saved.id, ok=True, status=200, latency_ms=5, error=None)
        clock.now = NOW
        probes.record_result(saved.id, ok=True, status=200, latency_ms=5, error=None)

        dropped = probes.purge()

        assert dropped >= 2
        assert probes.uptime(saved.id, DAILY_KEEP_DAYS).checks == 2  # the very old day went
        assert len(probes.recent(saved.id, limit=50)) == 1  # so did the old answers


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        super().__init__(body)
        self.status = status


def opener_for(
    body: bytes = b"ok", status: int = 200, *, error: BaseException | None = None
) -> Any:
    seen: list[Request] = []

    def open_(request: Request, timeout: float | None = None) -> FakeResponse:
        seen.append(request)
        if error is not None:
            raise error
        if status >= 400:
            raise HTTPError(request.full_url, status, "Reason", {}, io.BytesIO(body))  # type: ignore[arg-type]
        return FakeResponse(body, status)

    open_.seen = seen  # type: ignore[attr-defined]
    return open_


class TestAsking:
    def test_a_good_answer(self) -> None:
        opener = opener_for(b"all good")

        answer = ask(probe(), opener=opener)

        assert answer.ok and answer.status == 200 and answer.error is None
        assert answer.latency_ms is not None
        assert opener.seen[0].get_method() == "GET"
        assert opener.seen[0].get_header("User-agent").startswith("noust-probe/")

    def test_a_status_that_is_not_wanted_is_a_bad_answer_with_its_status(self) -> None:
        answer = ask(probe(), opener=opener_for(b"down", 503))

        assert not answer.ok and answer.status == 503 and answer.error == "HTTP 503"

    def test_a_404_is_fine_when_the_probe_says_so(self) -> None:
        assert ask(probe(expect_status="200-499"), opener=opener_for(b"", 404)).ok

    def test_the_word_the_page_must_contain(self) -> None:
        wanted = probe(expect_text="Add to basket")

        assert ask(wanted, opener=opener_for(b"<button>Add to basket</button>")).ok
        missing = ask(wanted, opener=opener_for(b"<h1>Maintenance</h1>"))
        assert not missing.ok and "expected text" in (missing.error or "")

    def test_the_word_is_only_looked_for_in_the_start_of_the_page(self) -> None:
        page = b"x" * (64 * 1024) + b"needle"

        assert not ask(probe(expect_text="needle"), opener=opener_for(page)).ok

    def test_a_head_probe_sends_a_head(self) -> None:
        opener = opener_for(b"")

        ask(probe(method="HEAD"), opener=opener)

        assert opener.seen[0].get_method() == "HEAD"

    @pytest.mark.parametrize(
        ("error", "said"),
        [
            (URLError(ConnectionRefusedError()), "connection refused"),
            (URLError(socket.gaierror("no name")), "the name does not resolve"),
            (URLError(TimeoutError()), "timed out"),
            (TimeoutError(), "timed out"),
            (URLError(ConnectionResetError()), "connection reset"),
            (URLError(ssl.SSLCertVerificationError(1, "certificate has expired")), "certificate"),
        ],
    )
    def test_a_failure_is_an_answer_that_says_why_in_one_line(
        self, error: BaseException, said: str
    ) -> None:
        answer = ask(probe(), opener=opener_for(error=error))

        assert not answer.ok and said in (answer.error or "")
        assert "\n" not in (answer.error or "")

    def test_an_address_on_the_machines_own_network_is_refused_before_a_request_is_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("10.0.0.5",))
        opener = opener_for()

        answer = ask(probe(url="https://intranet.example.com/"), opener=opener)

        assert not answer.ok
        assert "private or internal network" in (answer.error or "")
        assert opener.seen == []

    def test_a_name_that_does_not_exist_says_so_in_the_same_words_as_a_resolver_would(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The guard resolves the name before the request, so this is what a real
        # missing name looks like by the time it reaches the probe.
        def broken(_host: str) -> tuple[str, ...]:
            raise socket.gaierror(-2, "Name or service not known")

        monkeypatch.setattr(notifier_module, "_resolve_host", broken)
        opener = opener_for()

        answer = ask(probe(url="https://no-such-name.example.invalid/?key=SECRET"), opener=opener)

        assert answer.error == "the name does not resolve"
        assert opener.seen == []

    def test_the_error_never_quotes_the_address(self) -> None:
        secret = probe(url="https://shop.example.com/health?key=SECRET")

        answer = ask(
            secret,
            opener=opener_for(error=URLError("bad https://shop.example.com/health?key=SECRET")),
        )

        assert "SECRET" not in (answer.error or "")

    def test_a_slow_drip_cannot_hold_the_probe_past_its_timeout(self) -> None:
        reads = {"n": 0}

        class Drip(FakeResponse):
            def read(self, size: int = -1) -> bytes:
                reads["n"] += 1
                now["t"] += 4.0
                return b"x"

        now = {"t": 0.0}

        def open_(request: Request, timeout: float | None = None) -> Drip:
            return Drip(b"")

        answer = ask(
            probe(expect_text="never", timeout_s=10, interval_s=60),
            opener=open_,
            clock=lambda: now["t"],
        )

        assert not answer.ok
        assert reads["n"] <= 4

    def test_an_address_shown_publicly_has_no_query(self) -> None:
        assert (
            public_address("https://a.example.com/x?key=SECRET#frag") == "https://a.example.com/x"
        )

    def test_describe_error_has_a_fallback_for_what_it_does_not_know(self) -> None:
        assert describe_error(OSError("odd thing\nsecond line")) == "odd thing"


class Told:
    """What the engine published, composed in English."""

    def __init__(self) -> None:
        self.context = NotificationContext(locale="en", server="web-1", public_url="")
        self.sent: list[Notification] = []

    def __call__(self, build: Any) -> None:
        self.sent.append(build(self.context))


class Script:
    """Answers a probe with what a test scripts, one after the other."""

    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, one: Probe) -> Answer:
        self.asked.append(one.name)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


GOOD = Answer(True, 200, 120)
BAD = Answer(False, 503, 80, "HTTP 503")


def engine(
    probes: ProbeStore, clock: Clock, script: Script, told: Told, **kwargs: Any
) -> ProbeEngine:
    return ProbeEngine(probes, told, ask_one=script, clock=clock, **kwargs)


class TestTheStateMachine:
    def run(self, eng: ProbeEngine, clock: Clock, times: int, step: float = 60.0) -> None:
        for _ in range(times):
            eng.tick()
            clock.now += step

    def test_the_first_good_answer_is_up_and_says_nothing(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe())
        told, script = Told(), Script(GOOD)

        engine(probes, clock, script, told).tick()

        assert saved.id is not None
        assert probes.state(saved.id).state == "up"
        assert told.sent == []

    def test_down_needs_several_bad_answers_in_a_row(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe(fail_after=3))
        assert saved.id is not None
        told, script = Told(), Script(GOOD, BAD, BAD, GOOD)
        eng = engine(probes, clock, script, told)

        self.run(eng, clock, 4)

        assert probes.state(saved.id).state == "up"
        assert told.sent == []  # two bad answers were a blip

    def test_the_third_bad_answer_is_down_and_is_told_once(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe(fail_after=3, title="Shop"))
        assert saved.id is not None
        told, script = Told(), Script(GOOD, BAD)
        eng = engine(probes, clock, script, told)

        self.run(eng, clock, 7)

        state = probes.state(saved.id)
        assert state.state == "down" and state.failures >= 3
        (note,) = told.sent
        assert (note.kind, note.code, note.state.value) == (
            "probe_failed",
            "probe.failed",
            "failed",
        )
        assert note.subject == "shop"
        assert {f.key: f.value for f in note.facts}["address"] == URL
        assert note.excerpt is not None and "HTTP 503" in note.excerpt.lines
        (incident,) = probes.incidents(saved.id)
        assert incident.ended_at is None and incident.reason == "HTTP 503"

    def test_the_incident_begins_at_the_first_bad_answer_not_the_third(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe(fail_after=3))
        assert saved.id is not None
        told, script = Told(), Script(BAD)
        eng = engine(probes, clock, script, told)
        first = clock.now

        self.run(eng, clock, 3)

        assert probes.incidents(saved.id)[0].started_at == first

    def test_the_first_answer_being_bad_counts_towards_down(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe(fail_after=2))
        assert saved.id is not None
        told, script = Told(), Script(BAD)

        self.run(engine(probes, clock, script, told), clock, 2)

        assert probes.state(saved.id).state == "down"
        assert len(told.sent) == 1

    def test_an_answer_that_comes_back_is_told_once_with_how_long_it_was_down(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe(fail_after=2))
        assert saved.id is not None
        told, script = Told(), Script(BAD, BAD, BAD, GOOD, GOOD)
        eng = engine(probes, clock, script, told)

        self.run(eng, clock, 5)

        assert probes.state(saved.id).state == "up"
        assert [n.code for n in told.sent] == ["probe.failed", "probe.recovered"]
        facts = {f.key: f.value for f in told.sent[1].facts}
        assert facts["downtime"] == "3 min"  # from the first bad answer to the first good one
        assert probes.incidents(saved.id)[0].ended_at is not None

    def test_a_probe_is_asked_when_its_interval_has_passed_and_not_before(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        probes.save(probe(interval_s=60))
        told, script = Told(), Script(GOOD)
        eng = engine(probes, clock, script, told)

        eng.tick()
        clock.now += 59
        eng.tick()
        assert script.asked == ["shop"]
        clock.now += 1
        eng.tick()
        assert script.asked == ["shop", "shop"]

    def test_a_probe_switched_off_is_not_asked(self, probes: ProbeStore, clock: Clock) -> None:
        probes.save(probe(enabled=False))
        script = Script(GOOD)

        engine(probes, clock, script, Told()).tick()

        assert script.asked == []

    def test_every_answer_is_kept(self, probes: ProbeStore, clock: Clock) -> None:
        saved = probes.save(probe())
        assert saved.id is not None

        self.run(engine(probes, clock, Script(GOOD, BAD), Told()), clock, 2)

        assert [item["ok"] for item in probes.recent(saved.id)] == [True, False]
        assert probes.uptime(saved.id, 1).percent == 50.0

    def test_asking_now_ignores_the_interval(self, probes: ProbeStore, clock: Clock) -> None:
        saved = probes.save(probe(interval_s=3600))
        script = Script(GOOD)
        eng = engine(probes, clock, script, Told())
        eng.tick()

        check = eng.check_now(saved)

        assert script.asked == ["shop", "shop"]
        assert check.answer.ok


class TestOnAPool:
    class Inline:
        """An executor that runs the job when it is submitted and hands back a done future."""

        def __init__(self) -> None:
            self.jobs: list[Future[Any]] = []

        def submit(self, fn: Any, *args: Any) -> Future[Any]:
            future: Future[Any] = Future()
            future.set_result(fn(*args))
            self.jobs.append(future)
            return future

    def test_the_answer_is_applied_on_the_next_scan_and_not_asked_twice_meanwhile(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        saved = probes.save(probe())
        assert saved.id is not None
        script = Script(GOOD)
        eng = ProbeEngine(probes, Told(), ask_one=script, clock=clock, executor=self.Inline())  # type: ignore[arg-type]

        first = eng.tick()
        assert first == [] and probes.state(saved.id).state == "unknown"  # asked, not yet applied
        second = eng.tick()

        assert [c.probe.name for c in second] == ["shop"]
        assert probes.state(saved.id).state == "up"
        assert script.asked == ["shop"]

    def test_a_probe_still_being_asked_is_not_asked_again(
        self, probes: ProbeStore, clock: Clock
    ) -> None:
        probes.save(probe())
        pending: list[Future[Answer]] = []

        class Slow:
            def submit(self, fn: Any, *args: Any) -> Future[Answer]:
                future: Future[Answer] = Future()
                pending.append(future)
                return future

        eng = ProbeEngine(probes, Told(), ask_one=Script(GOOD), clock=clock, executor=Slow())  # type: ignore[arg-type]

        eng.tick()
        clock.now += 600
        eng.tick()

        assert len(pending) == 1


class TestTheWatch:
    @pytest.fixture
    def services(self, store: NoustStore, clock: Clock) -> Iterator[tuple[WatchServices, Told]]:
        told = Told()
        yield (
            WatchServices(
                publish=told,
                state=NotificationState(store, clock=clock),
                runner=FakeRunner(),
                config=_Settings(),  # type: ignore[arg-type]
                clock=clock,
            ),
            told,
        )

    def test_with_no_probe_it_does_nothing(
        self, probes: ProbeStore, services: tuple[WatchServices, Told]
    ) -> None:
        watch = ProbeWatch(probes)

        watch.check(services[0])

        assert watch._engine is None  # no pool is started for nobody

    def test_it_asks_the_probes_and_applies_their_answers(
        self,
        probes: ProbeStore,
        services: tuple[WatchServices, Told],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        saved = probes.save(probe(fail_after=1))
        assert saved.id is not None
        monkeypatch.setattr("noust.managers.probes.ask", lambda one, **k: BAD)
        watch = ProbeWatch(probes)
        try:
            watch.check(services[0])
            assert watch._executor is not None
            watch._executor.shutdown(wait=True)
            watch.check(services[0])
        finally:
            if watch._executor is not None:
                watch._executor.shutdown(wait=True)

        assert probes.state(saved.id).state == "down"
        assert [n.code for n in services[1].sent] == ["probe.failed"]


class _Settings:
    def get(self, key: str, default: Any = None) -> Any:
        return {"status_page.enabled": False}.get(key, default)


class TestTheManager:
    def test_saving_keeps_the_probe_and_who_saved_it(self, probes: ProbeStore) -> None:
        manager = ProbeManager(probes, ask_one=Script(GOOD))

        saved = manager.save(probe(url="https://shop.example.com/h?key=SECRET"), actor="ana")

        assert saved.id is not None and manager.remove("shop") is True

    def test_an_address_on_the_machines_own_network_is_refused_when_it_is_saved(
        self, probes: ProbeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("10.0.0.5",))

        with pytest.raises(ValidationError) as raised:
            ProbeManager(probes).save(probe(url="https://intranet.example.com/"))

        assert raised.value.field == "url" and "allow_private_hosts" in raised.value.details

    def test_a_name_that_does_not_resolve_yet_can_be_saved(
        self, probes: ProbeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(_host: str) -> tuple[str, ...]:
            raise OSError("Temporary failure in name resolution")

        monkeypatch.setattr(notifier_module, "_resolve_host", broken)

        assert ProbeManager(probes).save(probe()).id is not None

    def test_views_carry_the_state_and_the_uptime(self, probes: ProbeStore, clock: Clock) -> None:
        manager = ProbeManager(probes, ask_one=Script(GOOD), publish=Told(), clock=clock)
        manager.save(probe())

        manager.check("shop")
        (view,) = manager.views()

        body = view.to_dict()
        assert body["status"]["state"] == "up" and body["address"] == URL
        assert body["uptime"]["24h"]["percent"] == 100.0
        assert set(body["uptime"]) == {"24h", "7d", "30d", "90d"}

    def test_checking_one_that_does_not_exist_is_none(self, probes: ProbeStore) -> None:
        assert ProbeManager(probes).check("nope") is None
        assert ProbeManager(probes).incidents("nope") == []
