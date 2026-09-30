# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every deployment announces its start and its end, rollbacks included."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.logger import Logger
from noust.core.store import App, DeploymentTrigger, NoustStore
from noust.deployers import deploy_events
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from noust.deployers.recorder import DeploymentRecorder


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "wasm.db")
    instance.create_app(App(domain="shop.example.com", app_path=str(tmp_path / "app")))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def events() -> Iterator[list[DeployEvent]]:
    seen: list[DeployEvent] = []
    stop = deploy_events.subscribe(seen.append)
    yield seen
    stop()


def recorder(store: NoustStore, tmp_path: Path) -> DeploymentRecorder:
    return DeploymentRecorder(
        store,
        "shop.example.com",
        DeploymentTrigger.WEBHOOK.value,
        logger=Logger(verbose=False),
        log_root=tmp_path / "logs",
        git_info=lambda: ("abc1234", "main"),
        job_id="job-1",
    )


def test_success_is_announced_with_its_commit(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = recorder(store, tmp_path)
    rec.start(git_branch="main")
    rec.finish_success()

    assert [event.kind for event in events] == [DeployEventKind.STARTED, DeployEventKind.SUCCEEDED]
    done = events[-1]
    assert done.domain == "shop.example.com"
    assert done.deployment_id == rec.deployment_id
    assert done.trigger == "webhook"
    assert (done.commit, done.branch, done.job_id) == ("abc1234", "main", "job-1")
    assert done.error is None


def test_failure_and_rollback_are_told_apart(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    failed = recorder(store, tmp_path)
    failed.start()
    failed.finish_failure(DeploymentError("build failed", details="npm ERR!"))
    rolled = recorder(store, tmp_path)
    rolled.start()
    rolled.finish_failure(RolledBackError("Release 2 did not pass; release 1 is active again"))

    ends = [event for event in events if event.kind != DeployEventKind.STARTED]
    assert [event.kind for event in ends] == [DeployEventKind.FAILED, DeployEventKind.ROLLED_BACK]
    assert "npm ERR!" in (ends[0].error or "")
    assert "active again" in (ends[1].error or "")


def test_a_failing_subscriber_does_not_break_the_deployment(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent], caplog: pytest.LogCaptureFixture
) -> None:
    def broken(event: DeployEvent) -> None:
        raise RuntimeError("listener bug")

    stop = deploy_events.subscribe(broken)
    try:
        rec = recorder(store, tmp_path)
        rec.start()
        rec.finish_success()
    finally:
        stop()

    assert store.get_deployment(rec.deployment_id or 0).status == "success"
    assert len(events) == 2
    assert "listener bug" in caplog.text


def test_default_subscribers_are_loaded_once_and_missing_ones_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    import types

    module = types.ModuleType("wasm_test_listener")
    module.on_deploy_event = lambda event: calls.append(event.domain)  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "wasm_test_listener", module)
    monkeypatch.setattr(
        deploy_events, "DEFAULT_SUBSCRIBERS", ("wasm_test_listener", "wasm_no_such_module")
    )
    deploy_events.reset()
    deploy_events.suspend_defaults(False)

    deploy_events.publish(DeployEvent(kind=DeployEventKind.STARTED, domain="a.example.com"))
    deploy_events.publish(DeployEvent(kind=DeployEventKind.STARTED, domain="b.example.com"))

    assert calls == ["a.example.com", "b.example.com"]


def test_suspended_defaults_are_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    import types

    module = types.ModuleType("wasm_test_listener2")
    module.on_deploy_event = lambda event: calls.append(event.domain)  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "wasm_test_listener2", module)
    monkeypatch.setattr(deploy_events, "DEFAULT_SUBSCRIBERS", ("wasm_test_listener2",))
    deploy_events.reset()
    deploy_events.suspend_defaults(True)

    deploy_events.publish(DeployEvent(kind=DeployEventKind.STARTED, domain="a.example.com"))

    assert calls == []


# -- what a notification is composed from -------------------------------------


def _full_recorder(store: NoustStore, tmp_path: Path, **kwargs: object) -> DeploymentRecorder:
    return DeploymentRecorder(
        store,
        "shop.example.com",
        DeploymentTrigger.WEBHOOK.value,
        logger=Logger(verbose=False),
        log_root=tmp_path / "logs",
        git_info=lambda: ("abc1234", "main"),
        commit_message=lambda: "Fix cart total",
        release_id=lambda: "20260929-104449",
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_deployment_is_a_deploy_unless_it_says_otherwise(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = _full_recorder(store, tmp_path)
    rec.start()
    rec.finish_success()

    assert {event.operation for event in events} == {"deploy"}


def test_the_operation_is_announced_on_every_event(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = _full_recorder(store, tmp_path, operation="migrate")
    rec.start()
    rec.finish_success()

    assert [event.operation for event in events] == ["migrate", "migrate"]


def test_an_update_in_progress_names_the_operation_of_the_recorders_built_inside_it(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    with deploy_events.operation("update"):
        rec = _full_recorder(store, tmp_path)
    rec.start()
    rec.finish_success()

    assert {event.operation for event in events} == {"update"}
    assert deploy_events.current_operation() == "deploy"


def test_an_explicit_operation_beats_the_one_in_progress(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    with deploy_events.operation("update"):
        rec = _full_recorder(store, tmp_path, operation="activate")
    rec.start()
    rec.finish_success()

    assert {event.operation for event in events} == {"activate"}


def test_the_end_carries_what_the_notification_needs(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = _full_recorder(store, tmp_path)
    rec.start()
    rec.finish_success()

    done = events[-1]
    assert done.commit_message == "Fix cart total"
    assert done.release_id == "20260929-104449"
    assert done.duration_s is not None and 0 <= done.duration_s < 5
    assert events[0].duration_s is None


def test_a_failures_message_and_output_travel_apart(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = _full_recorder(store, tmp_path)
    rec.start()
    rec.finish_failure(DeploymentError("build failed", details="npm ERR! code 1"))

    failed = events[-1]
    assert failed.error_message == "build failed"
    assert failed.error_output == "npm ERR! code 1"
    # The whole failure stays what it was: GitHub's statuses read this one.
    assert "build failed" in (failed.error or "") and "npm ERR! code 1" in (failed.error or "")


def test_a_tools_output_wins_over_the_fix_hint(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = _full_recorder(store, tmp_path)
    rec.start()
    rec.finish_failure(
        DeploymentError("nginx test failed", details="Fix the syntax", output="nginx: [emerg] x")
    )

    assert events[-1].error_output == "nginx: [emerg] x"


def test_an_error_that_is_not_ours_is_all_message(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = _full_recorder(store, tmp_path)
    rec.start()
    rec.finish_failure(RuntimeError("kaboom"))

    assert events[-1].error_message == "kaboom"
    assert events[-1].error_output == ""


def test_a_secret_never_reaches_the_message_or_the_output(
    store: NoustStore, tmp_path: Path, events: list[DeployEvent]
) -> None:
    rec = _full_recorder(store, tmp_path)
    rec.start()
    rec._scrubber.add(["hunter2-secret-value"])
    rec.finish_failure(
        DeploymentError("login as hunter2-secret-value failed", details="pw=hunter2-secret-value")
    )

    failed = events[-1]
    assert "hunter2-secret-value" not in (failed.error_message or "")
    assert "hunter2-secret-value" not in (failed.error_output or "")
