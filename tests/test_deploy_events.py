# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every deployment announces its start and its end, rollbacks included."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from wasm.core.exceptions import DeploymentError, RolledBackError
from wasm.core.logger import Logger
from wasm.core.store import App, DeploymentTrigger, WASMStore
from wasm.deployers import deploy_events
from wasm.deployers.deploy_events import DeployEvent, DeployEventKind
from wasm.deployers.recorder import DeploymentRecorder


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "state" / "wasm.db")
    instance.create_app(App(domain="shop.example.com", app_path=str(tmp_path / "app")))
    yield instance
    WASMStore.reset_instance()


@pytest.fixture
def events() -> Iterator[list[DeployEvent]]:
    seen: list[DeployEvent] = []
    stop = deploy_events.subscribe(seen.append)
    yield seen
    stop()


def recorder(store: WASMStore, tmp_path: Path) -> DeploymentRecorder:
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
    store: WASMStore, tmp_path: Path, events: list[DeployEvent]
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
    store: WASMStore, tmp_path: Path, events: list[DeployEvent]
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
    store: WASMStore, tmp_path: Path, events: list[DeployEvent], caplog: pytest.LogCaptureFixture
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
