"""
Noust updating itself: the one command per installation method, run in its own unit.

What is pinned: nothing from a request reaches an argv (the command is the
method's, exactly); a container image and a source checkout are refused with
what to run instead; the installation runs in a transient unit with the answers
dpkg would ask for; the record survives the console and is settled by the
version the console that comes back runs; and ``/api/system/update``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.fs import RealFileSystem
from noust.core.runner import FakeRunner
from noust.managers import self_update
from noust.managers.self_update import SelfUpdate, SelfUpdateRecord, SelfUpdateRefused


def _manager(tmp_path: Path, runner: FakeRunner, **kw: Any) -> SelfUpdate:
    kw.setdefault("method", "apt")
    kw.setdefault("container", False)
    kw.setdefault("systemd", True)
    kw.setdefault("version", "3.1.0")
    kw.setdefault("process_started", 0.0)
    return SelfUpdate(
        runner=runner,
        fs=RealFileSystem(),
        record_path=tmp_path / "self-update.json",
        sleep=lambda seconds: None,
        **kw,
    )


def _systemd_run(runner: FakeRunner) -> tuple[str, ...]:
    return next(call for call in runner.calls if call[0] == "systemd-run")


class TestRefusals:
    def test_a_container_image_is_updated_by_replacing_it(self, tmp_path):
        refusal = _manager(tmp_path, FakeRunner(), container=True).refusal()

        assert refusal is not None and refusal.code == "container_image"
        assert "docker compose pull" in refusal.hint

    @pytest.mark.parametrize("method", ["source", "unknown"])
    def test_an_installation_it_does_not_update_says_what_to_run(self, tmp_path, method):
        refusal = _manager(tmp_path, FakeRunner(), method=method).refusal()

        assert refusal is not None and refusal.code == "unsupported_installation"
        assert refusal.hint.startswith("Update it on the server:")

    def test_the_container_is_asked_to_systemd(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self_update, "_DETECTED", {})
        runner = FakeRunner().script(["systemd-detect-virt", "-c"], stdout="docker\n")
        manager = SelfUpdate(runner=runner, record_path=tmp_path / "r.json", method="apt")

        assert manager.in_container()


class TestStart:
    def test_apt_refreshes_then_installs_in_its_own_unit(self, tmp_path):
        runner = FakeRunner()
        manager = _manager(tmp_path, runner)

        record = manager.start(job_id="j1", actor="ana", target_version="3.1.1")

        assert runner.calls[0] == ("apt-get", "update", "-q", "-o", "APT::Color=0")
        argv = _systemd_run(runner)
        assert argv[:3] == ("systemd-run", f"--unit=noust-self-update-{record.id}", "--collect")
        assert "--setenv=DEBIAN_FRONTEND=noninteractive" in argv
        command = argv[argv.index("--") + 1 :]
        assert command == (
            "apt-get",
            "-y",
            "-q",
            "-o",
            "Dpkg::Options::=--force-confdef",
            "-o",
            "Dpkg::Options::=--force-confold",
            "-o",
            "Dpkg::Use-Pty=0",
            "install",
            "--only-upgrade",
            "noust",
        )
        stored = json.loads((tmp_path / "self-update.json").read_text())
        assert (stored["status"], stored["job_id"], stored["from_version"]) == (
            "running",
            "j1",
            "3.1.0",
        )
        assert stored["unit"] == f"noust-self-update-{record.id}"

    @pytest.mark.parametrize(
        ("method", "command"),
        [
            ("dnf", ("dnf", "-y", "upgrade", "--refresh", "noust")),
            ("zypper", ("zypper", "--non-interactive", "update", "noust")),
            ("pipx", ("pipx", "upgrade", "noust")),
        ],
    )
    def test_each_method_has_its_one_command(self, tmp_path, monkeypatch, method, command):
        monkeypatch.setattr(self_update, "web_installed", lambda: False)
        runner = FakeRunner()
        _manager(tmp_path, runner, method=method).start()

        argv = _systemd_run(runner)
        assert argv[argv.index("--") + 1 :] == command

    def test_pip_is_this_interpreters(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self_update, "web_installed", lambda: False)
        runner = FakeRunner()
        _manager(tmp_path, runner, method="pip").start()

        argv = _systemd_run(runner)
        assert argv[argv.index("--") + 1 :][1:] == ("-m", "pip", "install", "--upgrade", "noust")

    def test_pip_keeps_the_console_with_what_it_needs_now(self, tmp_path, monkeypatch):
        """A new release may add to the web extra; a plain upgrade would leave it out."""
        monkeypatch.setattr(self_update, "web_installed", lambda: True)
        runner = FakeRunner()
        _manager(tmp_path, runner, method="pip").start()

        argv = _systemd_run(runner)
        assert argv[argv.index("--") + 1 :][1:] == (
            "-m",
            "pip",
            "install",
            "--upgrade",
            "noust[web]",
        )

    def test_pipx_upgrades_inside_its_own_environment_with_the_web_extra(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(self_update, "web_installed", lambda: True)
        runner = FakeRunner()
        _manager(tmp_path, runner, method="pipx").start()

        argv = _systemd_run(runner)
        assert argv[argv.index("--") + 1 :] == (
            "pipx",
            "runpip",
            "noust",
            "install",
            "--upgrade",
            "noust[web]",
        )

    def test_the_status_shows_the_command_that_will_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self_update, "web_installed", lambda: True)
        status = _manager(tmp_path, FakeRunner(), method="pip").status()

        assert status["command"][-1] == "noust[web]"

    def test_a_refresh_that_fails_installs_nothing(self, tmp_path):
        runner = FakeRunner().script(["apt-get", "update"], exit_code=100, stderr="E: repo is gone")

        with pytest.raises(SelfUpdateRefused) as caught:
            _manager(tmp_path, runner).start()

        assert caught.value.code == "refresh_failed"
        assert "repo is gone" in (caught.value.output or "")
        assert not any(call[0] == "systemd-run" for call in runner.calls)

    def test_one_update_at_a_time(self, tmp_path):
        runner = FakeRunner().script(["systemctl", "show"], stdout="ActiveState=active\n")
        manager = _manager(tmp_path, runner)
        manager.start()

        with pytest.raises(SelfUpdateRefused) as caught:
            manager.start()
        assert caught.value.code == "already_running"

    def test_without_systemd_it_runs_here(self, tmp_path):
        runner = FakeRunner()
        record = _manager(tmp_path, runner, systemd=False, method="dnf").start()

        assert record.unit is None
        assert record.status == "succeeded"
        assert ("dnf", "-y", "upgrade", "--refresh", "noust") in runner.calls

    def test_a_refused_unit_is_said_so(self, tmp_path):
        runner = FakeRunner().script(
            ["systemd-run"], exit_code=1, stderr="Failed to start transient service"
        )

        with pytest.raises(SelfUpdateRefused) as caught:
            _manager(tmp_path, runner).start()

        assert caught.value.code == "unit_failed"
        assert "transient service" in (caught.value.output or "")


class TestSettle:
    def _record(self, tmp_path: Path, **kw: Any) -> SelfUpdateRecord:
        record = SelfUpdateRecord(
            id="abcd1234",
            method="apt",
            from_version="3.1.0",
            argv=["apt-get"],
            unit="noust-self-update-abcd1234",
            started_at="2026-09-29T10:00:00+00:00",
            **kw,
        )
        (tmp_path / "self-update.json").write_text(json.dumps(record.to_dict()))
        return record

    def test_the_console_that_comes_back_on_a_new_version_says_it_succeeded(self, tmp_path):
        self._record(tmp_path)
        runner = FakeRunner().script(["systemctl", "show"], stdout="ActiveState=inactive\n")

        status = _manager(tmp_path, runner, version="3.1.1", process_started=time.time()).status()

        run = status["last_run"]
        assert (run["status"], run["to_version"]) == ("succeeded", "3.1.1")

    def test_systemds_verdict_is_a_failure_with_its_words(self, tmp_path):
        self._record(tmp_path)
        runner = (
            FakeRunner()
            .script(["systemctl", "show"], stdout="ActiveState=failed\n")
            .script(
                ["journalctl"],
                stdout="E: Sub-process /usr/bin/dpkg returned an error code (1)\n"
                "noust-self-update-abcd1234.service: Failed with result 'exit-code'.\n",
            )
        )

        run = _manager(tmp_path, runner).status()["last_run"]

        assert run["status"] == "failed"
        assert any("dpkg returned" in line for line in run["tail"])

    def test_a_console_restarted_on_the_same_version_says_nothing_was_newer(self, tmp_path):
        self._record(tmp_path)
        runner = FakeRunner().script(["systemctl", "show"], stdout="ActiveState=inactive\n")

        run = _manager(tmp_path, runner, process_started=time.time()).status()["last_run"]

        assert run["status"] == "failed"
        assert "offered nothing newer" in run["error"]

    def test_the_console_that_started_it_can_only_say_installed(self, tmp_path):
        self._record(tmp_path)
        runner = FakeRunner().script(["systemctl", "show"], stdout="ActiveState=inactive\n")

        run = _manager(tmp_path, runner, process_started=0.0).status()["last_run"]

        assert run["status"] == "installed"

    def test_a_running_unit_is_left_running(self, tmp_path):
        self._record(tmp_path)
        runner = FakeRunner().script(["systemctl", "show"], stdout="ActiveState=active\n")

        assert _manager(tmp_path, runner).status()["last_run"]["status"] == "running"

    def test_follow_restarts_the_console_for_pip(self, tmp_path):
        runner = FakeRunner().script(["systemctl", "show"], stdout="ActiveState=inactive\n")
        manager = _manager(tmp_path, runner, method="pip")
        record = manager.start()
        lines: list[str] = []

        settled = manager.follow(record, lines.append)

        assert settled.status == "installed"
        restart = [
            call for call in runner.calls if call[0] == "systemd-run" and "--on-active=3" in call
        ]
        assert restart and restart[0][-3:] == ("systemctl", "try-restart", "noust-web.service")


class TestApi:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from noust.web.api import system
        from noust.web.api.deps import require_auth, require_elevated

        runner = FakeRunner()
        monkeypatch.setattr(
            self_update,
            "SelfUpdate",
            lambda: _manager(tmp_path, runner, method="source"),
        )
        app = FastAPI()
        app.include_router(system.router, prefix="/api/system")
        app.dependency_overrides[require_auth] = lambda: {"type": "session"}
        app.dependency_overrides[require_elevated] = lambda: {"type": "session"}
        return TestClient(app)

    def test_it_says_why_it_cannot(self, client):
        body = client.get("/api/system/update").json()

        assert (body["supported"], body["code"], body["method"]) == (
            False,
            "unsupported_installation",
            "source",
        )
        assert body["command"] is None

    def test_starting_it_is_refused_with_the_command_to_run(self, client):
        response = client.post("/api/system/update")

        assert response.status_code == 501
        assert response.json()["detail"]["error"] == "unsupported_installation"

    def test_its_permissions_are_mapped(self):
        from noust.web.permissions.routes_system import ROUTES

        assert ROUTES[("GET", "/api/system/update")] == "server.read"
        assert ROUTES[("POST", "/api/system/update")] == "server.manage"
