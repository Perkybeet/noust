"""
Tests for the open terminals: the registry that is the one way to open one, and
the session it hands back.

What is pinned here is what a session leaves and what it is held to: the two
audit events that bracket it, the limits it lives under (in all, for one
person, and the server's shell only when it is allowed), the recording it
cannot be opened without, what happens when the connection drops and comes
back, and that nothing outlives the console.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.audit import Actor
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.runner import FakePty, FakeRunner
from noust.core.store import NoustStore
from noust.terminal import recording as recordings
from noust.terminal import sessions as sessions_module
from noust.terminal.sessions import (
    BACKLOG_BYTES,
    END_DISCONNECTED,
    MAX_INPUT_CHARS,
    SessionRegistry,
    TerminalDisabledError,
    TerminalGone,
    TerminalLimitError,
    reset_registry,
    shutdown_sessions,
)
from noust.terminal.settings import TerminalSettings
from noust.terminal.targets import SessionPlan, TerminalTarget
from noust.terminal.tickets import TicketStore


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Listener:
    """What a WebSocket would receive, collected from the reader's thread."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self, message: dict[str, Any]) -> None:
        with self._lock:
            self.messages.append(message)

    def of(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return [m for m in self.messages if m["type"] == kind]

    @property
    def text(self) -> str:
        return "".join(m["data"] for m in self.of("output"))


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


def settings(**overrides: Any) -> TerminalSettings:
    return replace(TerminalSettings(), **overrides)


@pytest.fixture
def registry(store, runner):
    made = SessionRegistry(settings=lambda: settings(), runner=lambda: runner)
    yield made
    made.shutdown()


APP_TARGET = TerminalTarget(
    kind="app",
    domain="shop.example.com",
    service=None,
    label="shop.example.com",
    account="noust-app-shop",
)
SERVER_TARGET = TerminalTarget(
    kind="server", domain=None, service=None, label="server", account="root"
)


def plan(target: TerminalTarget = APP_TARGET) -> SessionPlan:
    return SessionPlan(target=target, argv=("/bin/bash", "-i"))


def grant(
    *,
    kind: str = "app",
    actor: str = "maria",
    sid: str = "session-1",
    domain: str | None = "shop.example.com",
):
    _ticket, minted = TicketStore().mint(
        kind=kind,
        domain=domain,
        service=None,
        shell_name=None,
        rows=24,
        cols=80,
        sid=sid,
        actor=actor,
        client_ip="203.0.113.7",
    )
    return minted


def person(name: str = "maria") -> Actor:
    return Actor(kind="user", id="7", name=name, via="web")


def open_session(registry: SessionRegistry, listener: Listener | None = None, **kwargs: Any):
    listener = listener or Listener()
    target = kwargs.pop("target", APP_TARGET)
    session = registry.open(
        grant(domain=target.domain, **kwargs),
        plan(target),
        actor=person(kwargs.get("actor", "maria")),
    )
    session.start(listener)
    return session, listener


class TestWhatABeginningAndAnEndLeave:
    def test_a_session_opens_on_the_terminal_the_plan_asks_for(self, registry, runner):
        session, _ = open_session(registry)

        assert runner.ptys[0].argv == ("/bin/bash", "-i")
        assert (runner.ptys[0].rows, runner.ptys[0].cols) == (24, 80)
        assert (runner.ptys[0].max_seconds, runner.ptys[0].idle_seconds) == (
            settings().max_seconds,
            settings().idle_seconds,
        )
        assert registry.get(session.id) is session

    def test_the_start_is_on_record_with_who_what_and_for_how_long(self, registry):
        session, _ = open_session(registry)

        event = audit.get_log().read(action="terminal.start")[0]

        assert event["actor"] == "maria"
        assert event["resource"] == "app:shop.example.com"
        assert event["details"]["session"] == session.id
        assert event["details"]["account"] == "noust-app-shop"
        assert event["details"]["client_ip"] == "203.0.113.7"
        assert event["details"]["idle_seconds"] == settings().idle_seconds
        assert event["details"]["max_seconds"] == settings().max_seconds
        assert event["details"]["recording"] == session.recorder.id

    def test_the_end_is_on_record_with_the_digest_of_the_recording(self, registry, runner):
        session, listener = open_session(registry)
        runner.ptys[0].feed("hello\r\n")
        assert wait_for(lambda: "hello" in listener.text)

        runner.ptys[0].die(3)
        assert session.wait(5)

        event = audit.get_log().read(action="terminal.end")[0]
        cast = session.recorder.path.read_bytes()
        import hashlib

        assert event["details"]["session"] == session.id
        assert event["details"]["reason"] == "exited"
        assert event["details"]["exit_code"] == 3
        assert event["details"]["recording"] == session.recorder.id
        assert event["details"]["recording_sha256"] == hashlib.sha256(cast).hexdigest()
        assert event["details"]["recording_bytes"] == len(cast)
        assert event["details"]["recording_truncated"] is False
        assert event["details"]["duration"] >= 0
        assert event["resource"] == "app:shop.example.com"

    def test_the_server_is_a_target_of_its_own_in_the_trail(self, store, runner):
        registry = SessionRegistry(
            settings=lambda: settings(server_enabled=True), runner=lambda: runner
        )
        session, _ = open_session(registry, kind="server", target=SERVER_TARGET)

        assert audit.get_log().read(action="terminal.start")[0]["resource"] == "server"
        registry.shutdown()
        assert session.ended

    def test_an_ended_session_is_forgotten(self, registry, runner):
        session, _ = open_session(registry)
        runner.ptys[0].die(0)
        assert session.wait(5)

        assert registry.get(session.id) is None
        assert registry.list() == []

    def test_the_listener_hears_the_end(self, registry, runner):
        session, listener = open_session(registry)
        runner.ptys[0].die(0)
        assert session.wait(5)

        assert listener.of("ended") == [{"type": "ended", "reason": "exited", "exit_code": 0}]


class TestTheRecording:
    def test_what_is_typed_and_printed_is_recorded(self, registry, runner):
        session, listener = open_session(registry)
        runner.ptys[0].feed("$ ")
        assert wait_for(lambda: listener.text == "$ ")
        session.write("ls\r")
        runner.ptys[0].die(0)
        assert session.wait(5)

        events = recordings.read_recording(session.recorder.id, session.recorder.directory).events
        assert [(kind, data) for _t, kind, data in events] == [("o", "$ "), ("i", "ls\r")]
        assert runner.ptys[0].written == [b"ls\r"]

    def test_a_secret_of_the_application_never_reaches_the_file(self, store, runner):
        from noust.core.redact import Scrubber

        registry = SessionRegistry(settings=lambda: settings(), runner=lambda: runner)
        secret = "hunter2-hunter2-hunter2"
        planned = SessionPlan(target=APP_TARGET, argv=("bash",), scrubber=Scrubber([secret]))
        session = registry.open(grant(), planned, actor=person())
        listener = Listener()
        session.start(listener)

        runner.ptys[0].feed(f"DATABASE_PASSWORD={secret}\r\n")
        assert wait_for(lambda: secret in listener.text)  # the person sees their own secret
        runner.ptys[0].die(0)
        assert session.wait(5)

        assert secret not in session.recorder.path.read_text(encoding="utf-8")

    def test_what_is_typed_at_a_password_prompt_is_not_recorded(self, registry, runner):
        session, _ = open_session(registry)
        runner.ptys[0].hidden_input = True
        session.write("hunter2\r")
        runner.ptys[0].hidden_input = False
        session.write("whoami\r")
        runner.ptys[0].die(0)
        assert session.wait(5)

        text = session.recorder.path.read_text(encoding="utf-8")

        assert "hunter2" not in text and "whoami" in text
        assert runner.ptys[0].written == [b"hunter2\r", b"whoami\r"]  # the program still gets it

    def test_a_character_split_across_two_reads_is_decoded_whole(self, registry, runner):
        session, listener = open_session(registry)
        word = "señor".encode()
        runner.ptys[0].feed(word[:3])  # splits the two bytes of the ñ
        runner.ptys[0].feed(word[3:])
        assert wait_for(lambda: len(listener.text) >= 5)

        assert listener.text == "señor"
        runner.ptys[0].die(0)
        assert session.wait(5)
        assert "�" not in session.recorder.path.read_text(encoding="utf-8")

    def test_without_a_place_to_record_there_is_no_terminal(self, registry, runner, monkeypatch):
        monkeypatch.setattr(
            recordings,
            "SessionRecorder",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only file system")),
        )

        with pytest.raises(DeploymentError) as refused:
            registry.open(grant(), plan(), actor=person())

        assert "could not be recorded" in str(refused.value)
        assert runner.ptys == []  # not even the shell was started

    def test_a_terminal_that_could_not_be_opened_leaves_a_closed_recording(self, registry, runner):
        runner.pty = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ptys"))  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            registry.open(grant(), plan(), actor=person())

        info = recordings.list_recordings(recordings.recordings_dir())[0]
        assert info.status == "interrupted"


class TestTheLimits:
    def test_no_more_terminals_than_the_server_allows(self, store, runner):
        registry = SessionRegistry(settings=lambda: settings(max_sessions=2), runner=lambda: runner)
        open_session(registry, actor="ana")
        open_session(registry, actor="ben")

        with pytest.raises(TerminalLimitError) as refused:
            open_session(registry, actor="eva")

        assert "terminal.max_sessions" in str(refused.value.details)
        registry.shutdown()

    def test_no_more_than_a_person_may_have(self, store, runner):
        registry = SessionRegistry(
            settings=lambda: settings(max_sessions_per_user=2), runner=lambda: runner
        )
        open_session(registry, actor="ana")
        open_session(registry, actor="ana")

        with pytest.raises(TerminalLimitError) as refused:
            open_session(registry, actor="ana")

        assert "one person" in str(refused.value)
        open_session(registry, actor="ben")  # somebody else is not held up
        registry.shutdown()

    def test_a_slot_comes_back_when_a_session_ends(self, store, runner):
        registry = SessionRegistry(settings=lambda: settings(max_sessions=1), runner=lambda: runner)
        first, _ = open_session(registry)
        runner.ptys[0].die(0)
        assert first.wait(5)

        second, _ = open_session(registry)
        assert registry.get(second.id) is second
        registry.shutdown()

    def test_terminals_can_be_turned_off(self, store, runner):
        registry = SessionRegistry(settings=lambda: settings(enabled=False), runner=lambda: runner)

        with pytest.raises(TerminalDisabledError, match="turned off"):
            open_session(registry)

    def test_the_servers_shell_is_off_until_it_is_turned_on(self, registry):
        with pytest.raises(TerminalDisabledError) as refused:
            open_session(registry, kind="server", target=SERVER_TARGET)

        assert "off" in str(refused.value)
        assert "terminal.server.enabled" in str(refused.value.details)

    def test_turning_the_servers_shell_off_does_not_turn_off_applications(self, store, runner):
        registry = SessionRegistry(
            settings=lambda: settings(enabled=False, server_enabled=True), runner=lambda: runner
        )

        open_session(registry, kind="server", target=SERVER_TARGET)  # the server's own switch
        with pytest.raises(TerminalDisabledError):
            open_session(registry)
        registry.shutdown()

    def test_the_profile_keeps_the_servers_shell_off_unless_forced(self, store, runner):
        refused = SessionRegistry(
            settings=lambda: settings(server_enabled=True, ens=True), runner=lambda: runner
        )
        with pytest.raises(TerminalDisabledError, match="compliance controls"):
            open_session(refused, kind="server", target=SERVER_TARGET)

        forced = SessionRegistry(
            settings=lambda: settings(server_enabled=True, server_forced=True, ens=True),
            runner=lambda: runner,
        )
        open_session(forced, kind="server", target=SERVER_TARGET)
        forced.shutdown()

    def test_the_room_can_be_asked_before_a_ticket_is_minted(self, registry):
        assert registry.check_room("maria", kind="app").enabled is True
        with pytest.raises(TerminalDisabledError):
            registry.check_room("maria", kind="server")

    def test_input_is_a_paste_not_a_flood(self, registry):
        session, _ = open_session(registry)

        with pytest.raises(ValidationError):
            session.write("x" * (MAX_INPUT_CHARS + 1))
        session.write("x" * MAX_INPUT_CHARS)

    def test_the_runners_idle_limit_ends_the_session(self, store, runner):
        clock = Clock()
        runner.pty_clock = clock
        registry = SessionRegistry(
            settings=lambda: settings(idle_seconds=600, max_seconds=7200), runner=lambda: runner
        )
        session, listener = open_session(registry)

        clock.advance(601)

        assert session.wait(5)
        assert listener.of("ended")[0]["reason"] == "idle"
        assert audit.get_log().read(action="terminal.end")[0]["details"]["reason"] == "idle"

    def test_the_runners_deadline_ends_the_session(self, store, runner):
        clock = Clock()
        runner.pty_clock = clock
        registry = SessionRegistry(
            settings=lambda: settings(idle_seconds=7200, max_seconds=3600), runner=lambda: runner
        )
        session, listener = open_session(registry)
        for _ in range(7):
            clock.advance(600)
            session.write("x")

        assert session.wait(5)
        assert listener.of("ended")[0]["reason"] == "limit"

    def test_the_person_is_told_a_minute_before_and_not_again(self, store, runner):
        clock = Clock()
        runner.pty_clock = clock
        registry = SessionRegistry(
            settings=lambda: settings(idle_seconds=600, max_seconds=7200), runner=lambda: runner
        )
        session, listener = open_session(registry)

        clock.advance(560)
        assert wait_for(lambda: listener.of("notice"))
        clock.advance(10)
        time.sleep(1.2)
        assert [(n["kind"], n["seconds"] <= 60) for n in listener.of("notice")] == [("idle", True)]

        session.write("x")  # typing restarts it, and the warning can come again
        clock.advance(560)
        assert wait_for(lambda: len(listener.of("notice")) == 2)
        registry.shutdown()

    def test_what_the_header_shows_is_what_is_left(self, store, runner):
        clock = Clock()
        runner.pty_clock = clock
        registry = SessionRegistry(
            settings=lambda: settings(idle_seconds=600, max_seconds=3600), runner=lambda: runner
        )
        session, _ = open_session(registry)
        clock.advance(100)

        info = session.info()

        assert info.idle_remaining == pytest.approx(500)
        assert info.ends_at == pytest.approx(session.started_at + 3600)
        assert (info.rows, info.cols) == (24, 80)
        registry.shutdown()

    def test_a_resize_is_passed_on_and_recorded_but_is_not_typing(self, store, runner):
        clock = Clock()
        runner.pty_clock = clock
        registry = SessionRegistry(
            settings=lambda: settings(idle_seconds=600, max_seconds=3600), runner=lambda: runner
        )
        session, _ = open_session(registry)
        clock.advance(300)

        session.resize(50, 132)

        assert runner.ptys[0].resizes == [(50, 132)]
        assert runner.ptys[0].idle_remaining() == pytest.approx(300)
        assert (session.rows, session.cols) == (50, 132)
        registry.shutdown()
        events = recordings.read_recording(session.recorder.id, session.recorder.directory).events
        assert ("r", "132x50") in [(kind, data) for _t, kind, data in events]


class TestWhenTheConnectionDrops:
    def test_the_program_keeps_running_and_its_output_waits(self, registry, runner):
        session, listener = open_session(registry)
        session.detach()

        runner.ptys[0].feed("while you were away\r\n")
        time.sleep(0.2)

        assert listener.text == ""
        assert session.attached is False
        assert registry.get(session.id) is session

        again = Listener()
        lost = session.attach(again)

        assert lost is False
        assert again.text == "while you were away\r\n"
        assert session.attached is True

    def test_what_came_after_the_return_arrives_in_order(self, registry, runner):
        session, _ = open_session(registry)
        session.detach()
        runner.ptys[0].feed("one ")
        time.sleep(0.2)
        again = Listener()
        session.attach(again)
        runner.ptys[0].feed("two")

        assert wait_for(lambda: again.text == "one two")

    def test_an_absence_longer_than_the_backlog_says_it_lost_output(self, registry, runner):
        session, _ = open_session(registry)
        session.detach()
        chunk = "x" * 32_768
        for _ in range(BACKLOG_BYTES // len(chunk) + 4):
            runner.ptys[0].feed(chunk)
        time.sleep(0.4)

        again = Listener()
        lost = session.attach(again)

        assert lost is True
        assert 0 < len(again.text) <= BACKLOG_BYTES

    def test_the_new_connection_takes_the_session_over_and_the_old_is_told(self, registry):
        session, first = open_session(registry)
        second = Listener()

        session.attach(second)

        assert first.of("error") == [{"type": "error", "message": "Taken over by another window"}]
        assert session.attached is True

    def test_a_connection_that_left_after_another_took_over_does_not_detach_it(self, registry):
        session, first = open_session(registry)
        second = Listener()
        session.attach(second)

        session.detach(first)

        assert session.attached is True

    def test_a_session_nobody_comes_back_to_ends_as_disconnected(
        self, registry, runner, monkeypatch
    ):
        monkeypatch.setattr(sessions_module, "DETACH_GRACE_SECONDS", 0.3)
        session, _ = open_session(registry)
        session.detach()

        assert session.wait(5)

        event = audit.get_log().read(action="terminal.end")[0]
        assert event["details"]["reason"] == END_DISCONNECTED
        assert runner.ptys[0].terminated is True

    def test_only_its_owner_can_come_back(self, registry):
        session, _ = open_session(registry, sid="session-1", actor="maria")

        assert registry.resumable(session.id, sid="session-1", actor="maria") is session
        with pytest.raises(TerminalGone):
            registry.resumable(session.id, sid="session-2", actor="maria")
        with pytest.raises(TerminalGone):
            registry.resumable(session.id, sid="session-1", actor="pedro")
        with pytest.raises(TerminalGone):
            registry.resumable("0" * 16, sid="session-1", actor="maria")

    def test_an_ended_session_cannot_be_come_back_to(self, registry, runner):
        session, _ = open_session(registry)
        runner.ptys[0].die(0)
        assert session.wait(5)

        with pytest.raises(TerminalGone):
            registry.resumable(session.id, sid="session-1", actor="maria")

    def test_a_connection_that_is_gone_detaches_the_session_and_loses_nothing(
        self, registry, runner
    ):
        calls: list[int] = []

        def broken(message: dict[str, Any]) -> None:
            calls.append(1)
            raise RuntimeError("event loop is closed")

        session = registry.open(grant(), plan(), actor=person())
        session.start(broken)
        runner.ptys[0].feed("a")
        assert wait_for(lambda: session.attached is False)
        runner.ptys[0].feed("b")
        time.sleep(0.3)

        assert not session.ended
        assert len(calls) == 1  # it is not tried again until somebody attaches
        again = Listener()
        session.attach(again)
        assert again.text == "ab"

    def test_a_connection_that_does_not_answer_in_time_is_treated_as_gone(self, registry, runner):
        def stuck(message: dict[str, Any]) -> None:
            raise TimeoutError("the browser is not reading")

        session = registry.open(grant(), plan(), actor=person())
        session.start(stuck)
        runner.ptys[0].feed("kept")

        assert wait_for(lambda: session.attached is False)
        again = Listener()
        session.attach(again)
        assert again.text == "kept"

    def test_the_keyboard_is_not_held_up_by_a_slow_connection(self, registry, runner):
        release = threading.Event()
        started = threading.Event()

        def slow(message: dict[str, Any]) -> None:
            started.set()
            release.wait(5)

        session = registry.open(grant(), plan(), actor=person())
        session.start(slow)
        runner.ptys[0].feed("output the browser cannot keep up with")
        assert started.wait(5)

        before = time.monotonic()
        session.write("\x03")  # Ctrl+C while the output is stuck
        assert time.monotonic() - before < 1
        assert runner.ptys[0].written == [b"\x03"]
        release.set()


class TestEndingThem:
    def test_an_administrator_ends_somebody_elses_session(self, registry, runner):
        session, listener = open_session(registry, actor="maria")

        assert registry.kill(session.id, by=person("admin")) is True

        assert session.ended
        assert listener.of("ended")[0]["reason"] == "killed"
        kill = audit.get_log().read(action="terminal.kill")[0]
        assert kill["actor"] == "admin"
        assert kill["details"] == {"session": session.id, "opened_by": "maria"}

    def test_ending_one_that_is_not_there_says_so(self, registry):
        assert registry.kill("0" * 16, by=person()) is False

    def test_the_person_ends_their_own(self, registry):
        session, listener = open_session(registry)

        session.close()

        assert listener.of("ended")[0]["reason"] == "closed"

    def test_stopping_the_console_ends_every_session_and_marks_the_recordings(self, store, runner):
        registry = SessionRegistry(settings=lambda: settings(), runner=lambda: runner)
        reset_registry(registry)
        first, _ = open_session(registry, actor="ana")
        second, _ = open_session(registry, actor="ben")

        assert shutdown_sessions() == 2

        assert first.ended and second.ended
        statuses = {info.status for info in recordings.list_recordings(recordings.recordings_dir())}
        assert statuses == {"interrupted"}
        reset_registry(None)

    def test_the_session_list_says_who_has_what_open(self, registry):
        first, _ = open_session(registry, actor="ana")
        second, _ = open_session(registry, actor="ben")

        listed = registry.list()

        assert [info.actor for info in listed] == ["ana", "ben"]
        assert {info.id for info in listed} == {first.id, second.id}
        assert json.dumps(listed[0].to_dict())  # JSON-ready
        assert registry.active_recordings() == {first.recorder.id, second.recorder.id}


class TestTheFake:
    def test_a_fake_terminal_is_what_the_registry_opens_in_tests(self, registry, runner):
        open_session(registry)

        assert isinstance(runner.ptys[0], FakePty)
