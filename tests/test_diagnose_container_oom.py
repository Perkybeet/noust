# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The diagnosis asks where the processes are when an application runs in containers (item 175).

The unit of a Compose stack or of a container application is a ``oneshot`` that runs ``docker compose
up -d`` and stays: its cgroup holds no process, so reading the kernel's count of what it killed there
said nothing, and a container the kernel did kill was reported as "this server cannot say which
application it was". The containers are asked through the door of Docker (``noust.core.docker``): how
each one ended says whether the kernel killed it, and the cgroup of each running one carries the count
and the peak.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import AppRecord, Registry
from noust.core.teams import Scope
from tests.support.diagnose import (
    DOMAIN,
    UNIT_FILE,
    _app,
    _check,
    _diagnose,
    _fragment,
    _no_findings,
    _unit_state,
)
from tests.support.docker_machine import DockerMachine, full_id

MIB = 1024 * 1024
APP_PATH = "/var/www/apps/example-com"
WEB = "example-com-web-1"
WORKER = "example-com-worker-1"


@pytest.fixture
def machine() -> DockerMachine:
    return DockerMachine()


@pytest.fixture
def mount(tmp_path: Path) -> Path:
    """A cgroup v2 hierarchy: its root says which controllers exist."""
    (tmp_path / "cgroup.controllers").write_text("cpuset cpu io memory pids\n")
    return tmp_path


def _gate(machine: DockerMachine) -> DockerGate:
    registry = Registry(apps=(AppRecord(DOMAIN, APP_PATH),))
    return DockerGate(machine, registry=lambda: registry, scope=Scope.everything())


def _stack(machine: DockerMachine, **web: object) -> None:
    machine.box(
        WEB,
        project="example-com",
        service="web",
        working_dir=APP_PATH,
        state=str(web.pop("state", "running")),
        **web,  # type: ignore[arg-type]
    )
    machine.box(WORKER, project="example-com", service="worker", working_dir=APP_PATH)


def _cgroup(mount: Path, name: str, **files: str) -> Path:
    directory = mount / "system.slice" / f"docker-{full_id(name)}.scope"
    directory.mkdir(parents=True, exist_ok=True)
    for key, text in files.items():
        (directory / key.replace("_", ".", 1)).write_text(text)
    return directory


def _unit_of_a_stack(machine: DockerMachine, *, port: int | None = None) -> None:
    """The oneshot that started the stack: active and exited, with no process of its own."""
    _fragment(machine)
    _unit_state(machine, active="active", sub="exited")
    _no_findings(machine)
    if port is not None:
        machine.script(
            ["ss", "-ltnpH"],
            stdout=f'LISTEN 0 511 127.0.0.1:{port} 0.0.0.0:* users:(("node",pid=100,fd=18))\n',
        )


def _asked_the_unit_cgroup(machine: DockerMachine) -> bool:
    return any(
        call[:3] == ("systemctl", "show", "-p") and "ControlGroup" in call[3]
        for call in machine.calls
    )


class TestAStackWhoseContainerTheKernelKilled:
    def test_the_check_says_which_container_and_the_unit_is_not_asked_for_a_cgroup(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine, state="exited", oom=True, exit_code=137, memory=512 * MIB)
        _unit_of_a_stack(machine)

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        check = _check(result, "oom")
        assert check.status == "warn"
        assert WEB in check.summary and WORKER not in check.summary
        assert "for running out of memory" in check.summary
        assert f"memory.max={512 * MIB}" in check.evidence
        assert not _asked_the_unit_cgroup(machine)

    def test_a_container_that_stayed_down_makes_the_application_down_with_the_cause(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine, state="exited", oom=True, exit_code=137, memory=512 * MIB)
        _unit_of_a_stack(machine)

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        assert result.verdict == "down"
        cause = result.probable_cause or ""
        assert f"killed {WEB} of {DOMAIN} for running out of memory" in cause
        assert "its limit is 512.0 MB" in cause
        # A stack's limits are in its compose file: the command a cause names is one that works.
        assert "compose file" in cause
        assert f"noust update {DOMAIN}" in cause
        assert "noust app limits" not in cause

    def test_a_container_that_came_back_is_degraded_and_the_count_is_the_kernels(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine, oom=True, memory=512 * MIB)
        _unit_of_a_stack(machine, port=3000)
        _cgroup(
            mount,
            WEB,
            memory_events="oom_kill 2\n",
            memory_max=f"{512 * MIB}\n",
            memory_peak=f"{500 * MIB}\n",
        )

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        assert result.verdict == "degraded"
        assert "oom_kill=2" in _check(result, "oom").evidence
        assert f"memory.peak={500 * MIB}" in _check(result, "oom").evidence
        assert WEB in (result.probable_cause or "")

    def test_without_a_limit_it_is_the_server_that_ran_out(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine, state="exited", oom=True, exit_code=137)
        _unit_of_a_stack(machine)

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        cause = result.probable_cause or ""
        assert "no memory limit" in cause and "the server ran out of memory" in cause
        assert f"noust update {DOMAIN}" in cause

    def test_the_kernel_counting_in_the_cgroup_of_a_running_container_is_enough(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        # Docker's flag is of the last run only; the cgroup keeps counting while the container lives.
        _stack(machine, memory=256 * MIB)
        _unit_of_a_stack(machine, port=3000)
        _cgroup(mount, WEB, memory_events="oom_kill 1\n", memory_max=f"{256 * MIB}\n")

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        assert _check(result, "oom").status == "warn"
        assert WEB in _check(result, "oom").summary


class TestAStackNobodyKilled:
    def test_containers_that_ended_well_say_nothing_of_memory(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine)
        _unit_of_a_stack(machine, port=3000)

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        assert _check(result, "oom").status == "ok"

    def test_a_kernel_log_that_names_no_container_of_this_application_does_not_blame_it(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine)
        _unit_of_a_stack(machine, port=3000)
        machine.script(
            ["journalctl", "-k", "--since", "-7d", "--grep", "oom", "-o", "short-iso"],
            stdout="2026-11-10T03:12:00+00:00 host kernel: Out of memory: Killed process 100 (x)\n",
        )

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        check = _check(result, "oom")
        assert check.status == "warn" and WEB not in check.summary
        assert result.probable_cause is None

    def test_a_docker_that_does_not_answer_is_no_reason_to_fail_the_diagnosis(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine)
        _unit_of_a_stack(machine, port=3000)
        machine.down = True

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="docker-compose", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        assert _check(result, "oom").status in {"ok", "skip"}
        assert not _asked_the_unit_cgroup(machine)


class TestAContainerApplication:
    def test_its_limit_is_one_noust_changes_so_the_command_is_the_one_for_limits(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine, state="exited", oom=True, exit_code=137, memory=512 * MIB)
        _unit_of_a_stack(machine)

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="container", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        cause = result.probable_cause or ""
        assert "killed example.com for running out of memory: its limit is 512.0 MB" in cause
        assert f"noust app limits {DOMAIN} --memory 768M --restart" in cause
        assert not _asked_the_unit_cgroup(machine)

    def test_with_no_limit_and_no_reading_of_its_peak_the_cause_does_not_print_a_question_mark(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _stack(machine, state="exited", oom=True, exit_code=137)
        _unit_of_a_stack(machine)

        result = _diagnose(
            monkeypatch,
            machine,
            app=_app(app_type="container", app_path=APP_PATH, ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        cause = result.probable_cause or ""
        assert "no memory limit" in cause and "?" not in cause
        assert f"noust app limits {DOMAIN} --memory" in cause


class TestAnApplicationThatRunsAsAUnitIsAsBefore:
    def test_the_unit_is_asked_for_its_cgroup_and_docker_is_not_asked(
        self, monkeypatch: pytest.MonkeyPatch, machine: DockerMachine, mount: Path
    ) -> None:
        _fragment(machine)
        _unit_state(machine)
        machine.script(
            ["ss", "-ltnpH"],
            stdout='LISTEN 0 511 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=100,fd=18))\n',
        )
        _no_findings(machine)
        machine.script(
            ["systemctl", "show", "-p", "ControlGroup,ActiveEnterTimestamp,Result", UNIT_FILE],
            stdout="ControlGroup=\nActiveEnterTimestamp=\nResult=success\n",
        )

        _diagnose(
            monkeypatch,
            machine,
            app=_app(ssl_enabled=False),
            cgroup_mount=mount,
            docker=_gate(machine),
        )

        assert _asked_the_unit_cgroup(machine)
        assert not any(call[:2] == ("docker", "ps") for call in machine.calls)
