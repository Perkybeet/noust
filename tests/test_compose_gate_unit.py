# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The unit of a Compose stack holds its start to what was judged, and every path that starts a stack asks first.

The unit runs ``noust app compose gate`` before ``docker compose up`` (and before the rebuild of a
reload), so that whoever starts the stack, systemd at boot included, goes through the gate; and
:class:`~noust.managers.service_manager.ServiceManager` and the supervisor of a machine with no systemd ask
the same before they pass the start on, so a refusal leaves a running stack as it was.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from jinja2 import Environment, PackageLoader

from noust.core import compose_acceptance
from noust.core.exceptions import ServiceError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, AppStatus, AppType, NoustStore
from noust.deployers import compose_gate
from noust.deployers.container import ContainerDeployer
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import compose_privileges
from noust.deployers.helpers.compose_guard import inspect_compose
from noust.managers.service_manager import ServiceManager

DOMAIN = "stack.example.com"
UNIT = "stack-example-com"


@pytest.fixture
def jinja() -> Environment:
    return Environment(  # noqa: S701 - a unit file is not HTML
        loader=PackageLoader("noust", "templates/systemd"), trim_blocks=True, lstrip_blocks=True
    )


def lines(unit: str, directive: str) -> list[str]:
    return [line for line in unit.splitlines() if line.startswith(f"{directive}=")]


def render(jinja: Environment, **extra: Any) -> str:
    return jinja.get_template("docker-compose.service.j2").render(
        name=UNIT,
        description=f"Docker Compose app: {DOMAIN}",
        working_directory=f"/var/www/apps/{UNIT}",
        environment={},
        compose_file=None,
        **extra,
    )


GATE = (
    "/bin/sh -c '[ ! -x /usr/bin/noust ] || "
    f"exec /usr/bin/noust app compose gate {DOMAIN} --project {UNIT}'"
)


class TestTheUnit:
    def test_the_gate_runs_before_up_at_every_start(self, jinja: Environment) -> None:
        unit = render(jinja, compose_project=UNIT, gate=GATE)

        assert lines(unit, "ExecStartPre") == [f"ExecStartPre={GATE}"]
        assert unit.index("ExecStartPre=") < unit.index("ExecStart=/usr/bin/docker")

    def test_a_reload_passes_the_gate_before_it_rebuilds(self, jinja: Environment) -> None:
        unit = render(jinja, compose_project=UNIT, gate=GATE)

        reloads = lines(unit, "ExecReload")
        assert reloads == [
            f"ExecReload={GATE}",
            f"ExecReload=/usr/bin/docker compose -p {UNIT} up -d --remove-orphans --build",
        ]

    def test_the_project_is_passed_to_every_command(self, jinja: Environment) -> None:
        unit = render(jinja, compose_project=UNIT, gate=GATE)

        for directive in ("ExecStart=/usr/bin/docker", "ExecStop", "ExecReload=/usr/bin/docker"):
            line = next(line for line in unit.splitlines() if line.startswith(directive))
            assert f"-p {UNIT}" in line

    def test_a_unit_without_a_gate_is_as_it_was(self, jinja: Environment) -> None:
        unit = render(jinja, compose_project=None)

        assert lines(unit, "ExecStartPre") == []
        assert len(lines(unit, "ExecReload")) == 1

    def test_nothing_that_reaches_the_gate_line_starts_another_directive(
        self, jinja: Environment
    ) -> None:
        unit = render(jinja, compose_project=UNIT, gate="/usr/bin/noust x\nUser=nobody")

        assert lines(unit, "User") == ["User=root"]


class TestTheCommandTheUnitRuns:
    def stack(self) -> DockerComposeDeployer:
        deployer = DockerComposeDeployer(runner=FakeRunner())
        deployer.domain = DOMAIN
        return deployer

    def test_it_runs_the_command_by_its_path_only_where_it_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.core.utils.find_noust_executable", lambda: "/usr/bin/noust")

        said = compose_privileges.gate_command(self.stack(), UNIT)

        assert said == (
            "/bin/sh -c '[ ! -x /usr/bin/noust ] || "
            f"exec /usr/bin/noust app compose gate {DOMAIN} --project {UNIT}'"
        )

    def test_a_stack_that_leaves_the_project_to_compose_passes_none(self) -> None:
        said = compose_privileges.gate_command(self.stack(), None)

        assert said is not None and "--project" not in said

    def test_a_container_application_has_no_gate_because_its_file_is_noust_s(self) -> None:
        deployer = ContainerDeployer(runner=FakeRunner())
        deployer.domain = DOMAIN

        assert compose_privileges.gate_command(deployer, UNIT) is None


@pytest.mark.allow_subprocess
class TestWhatAShellMakesOfTheLine:
    """
    The line is run by systemd, which splits it as a shell does and executes ``/bin/sh``: here by the real
    thing, with a stand-in for Noust that says what it was asked and how it ends.
    """

    def argv(self, executable: Path) -> list[str]:
        return shlex.split(compose_privileges.unit_gate(str(executable), DOMAIN, UNIT))

    def standin(self, tmp_path: Path, status: int) -> Path:
        script = tmp_path / "noust"
        script.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/asked"\nexit {status}\n')
        script.chmod(0o755)
        return script

    def run(self, argv: list[str]) -> int:
        return subprocess.run(argv, capture_output=True, check=False).returncode

    def test_with_noust_there_the_gate_is_asked_and_its_answer_is_the_lines(
        self, tmp_path: Path
    ) -> None:
        script = self.standin(tmp_path, 0)

        assert self.run(self.argv(script)) == 0
        assert (tmp_path / "asked").read_text().split() == [
            "app",
            "compose",
            "gate",
            DOMAIN,
            "--project",
            UNIT,
        ]

    def test_a_gate_that_refuses_is_a_start_that_does_not_happen(self, tmp_path: Path) -> None:
        script = self.standin(tmp_path, 1)

        assert self.run(self.argv(script)) == 1

    def test_whatever_status_the_command_ends_with_is_the_lines(self, tmp_path: Path) -> None:
        # An older Noust that has no such command ends with 2: the stack does not start either.
        script = self.standin(tmp_path, 2)

        assert self.run(self.argv(script)) == 2

    def test_without_noust_the_stack_starts_as_it_did_before_4_3(self, tmp_path: Path) -> None:
        assert self.run(self.argv(tmp_path / "noust")) == 0
        assert not (tmp_path / "asked").exists()

    def test_a_link_left_dangling_by_pip_uninstall_is_no_noust_either(self, tmp_path: Path) -> None:
        link = tmp_path / "noust"
        link.symlink_to(tmp_path / "venv" / "bin" / "noust")

        assert self.run(self.argv(link)) == 0

    def test_a_file_that_is_not_executable_is_no_noust(self, tmp_path: Path) -> None:
        script = self.standin(tmp_path, 1)
        script.chmod(0o644)

        assert self.run(self.argv(script)) == 0


class Compose(FakeRunner):
    """A Compose that resolves the stack to a mount of ``source``."""

    def __init__(self, source: str) -> None:
        super().__init__()
        self.source = source

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        words = [str(word) for word in argv]
        if words[:2] == ["docker", "compose"] and "config" in words:
            self.calls.append(tuple(words))
            model = {
                "services": {
                    "web": {
                        "image": "nginx",
                        "volumes": [{"type": "bind", "source": self.source, "target": "/data"}],
                    }
                }
            }
            return CommandResult(tuple(words), 0, json.dumps(model), "")
        return super().run(argv, **kwargs)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()
    set_runner(None)


@pytest.fixture
def app(tmp_path: Path) -> Path:
    path = tmp_path / "stack-example-com"
    path.mkdir()
    (path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n    volumes:\n      - ${DATA:-./data}:/data\n"
    )
    return path


def accepted(app: Path, store: NoustStore) -> None:
    findings = inspect_compose(
        {
            "services": {
                "web": {
                    "image": "nginx",
                    "volumes": [{"type": "bind", "source": str(app / "data"), "target": "/data"}],
                }
            }
        },
        app,
    )
    compose_acceptance.record(
        DOMAIN, [findings.keys[s] for s in findings.refused], source="deploy", store=store
    )


class Ran:
    """What running the gate as a unit does came to: the exit code and what it printed."""

    def __init__(self, exit_code: int, output: str) -> None:
        self.exit_code = exit_code
        self.output = output


def gate(app: Path, capsys: pytest.CaptureFixture[str], *args: str) -> Ran:
    """Run ``noust app compose gate`` through the entry point, which prints an error and exits 1."""
    from noust.cli.app import main

    code = main(
        ["app", "compose", "gate", DOMAIN, "--directory", str(app), "--project", UNIT, *args]
    )
    seen = capsys.readouterr()
    return Ran(code, seen.out + seen.err)


class TestTheCommand:
    def test_a_stack_that_passes_exits_zero(
        self, app: Path, store: NoustStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        accepted(app, store)
        set_runner(Compose(str(app / "data")))

        result = gate(app, capsys)

        assert result.exit_code == 0, result.output

    def test_a_stack_that_was_never_recorded_exits_zero_and_says_so(
        self, app: Path, store: NoustStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        set_runner(Compose("/"))

        result = gate(app, capsys)

        assert result.exit_code == 0, result.output
        assert "no record" in result.output.lower()

    def test_a_stack_that_asks_for_the_root_exits_one_with_the_line_and_the_ways_out(
        self, app: Path, store: NoustStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        accepted(app, store)
        set_runner(Compose("/"))

        result = gate(app, capsys)

        assert result.exit_code == 1
        assert "was not started" in result.output
        assert "service web mounts /" in result.output
        assert f"noust update {DOMAIN}" in result.output
        assert f"noust app sandbox compose-exception {DOMAIN}" in result.output

    def test_the_directory_the_file_and_the_profiles_are_the_units(
        self, app: Path, store: NoustStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (app / "deploy").mkdir()
        (app / "deploy" / "compose.yml").write_text("services:\n  web:\n    image: nginx\n")
        compose_acceptance.record(DOMAIN, [], source="deploy", store=store)
        runner = Compose(str(app / "data"))
        set_runner(runner)

        result = gate(app, capsys, "--compose-file", "deploy/compose.yml", "--profiles", "web,db")

        assert result.exit_code == 0, result.output
        asked = next(call for call in runner.calls if "config" in call)
        assert str(app / "deploy" / "compose.yml") in asked
        assert asked[asked.index("-p") + 1] == UNIT

    def test_a_compose_file_that_leaves_the_tree_exits_one(
        self, app: Path, store: NoustStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        set_runner(Compose("/"))

        result = gate(app, capsys, "--compose-file", "../elsewhere.yml")

        assert result.exit_code == 1

    def test_a_store_that_is_busy_for_a_moment_is_asked_again_before_the_start_is_given_up(
        self,
        app: Path,
        store: NoustStore,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sqlite3

        from noust.cli.commands import app_compose

        accepted(app, store)
        set_runner(Compose(str(app / "data")))
        monkeypatch.setattr(app_compose, "GATE_PAUSE_SECONDS", 0)
        real = compose_gate.judge_from_unit
        asked: list[int] = []

        def busy_once(*args: Any, **kwargs: Any) -> Any:
            asked.append(1)
            if len(asked) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)

        monkeypatch.setattr(compose_gate, "judge_from_unit", busy_once)

        result = gate(app, capsys)

        assert result.exit_code == 0, result.output
        assert len(asked) == 2

    def test_the_gate_is_not_something_help_offers(self) -> None:
        from noust.cli.app import cli as root_cli

        result = CliRunner().invoke(root_cli, ["app", "compose", "--help"])

        assert " gate " not in result.output


def make_app(store: NoustStore, path: Path, *, kind: str = AppType.DOCKER_COMPOSE.value) -> App:
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type=kind,
            source="src",
            app_path=str(path),
            status=AppStatus.RUNNING.value,
        )
    )


class TestBeforeSystemd:
    """The service manager asks first: a refusal leaves a running stack running."""

    def manager(self, runner: FakeRunner) -> ServiceManager:
        return ServiceManager(runner=runner)

    def refuse(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        asked: list[str] = []

        def check(unit: str, **_: Any) -> None:
            asked.append(unit)
            raise ServiceError("stack.example.com was not started")

        monkeypatch.setattr(compose_gate, "check_before", check)
        return asked

    @pytest.mark.parametrize("action", ["start", "restart"])
    def test_a_refusal_never_reaches_systemctl(
        self, action: str, monkeypatch: pytest.MonkeyPatch, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        asked = self.refuse(monkeypatch)
        manager = self.manager(runner)
        monkeypatch.setattr(manager, "_require_managed", lambda *a, **k: _Unit(UNIT))

        with pytest.raises(ServiceError, match="was not started"):
            getattr(manager, action)(UNIT)

        assert asked == [UNIT]
        assert not [call for call in runner.calls if call[:1] == ("systemctl",)]

    @pytest.mark.parametrize("action", ["start", "restart"])
    def test_a_stack_that_passes_is_started_by_systemd(
        self, action: str, monkeypatch: pytest.MonkeyPatch, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        monkeypatch.setattr(compose_gate, "check_before", lambda *a, **k: None)
        manager = self.manager(runner)
        monkeypatch.setattr(manager, "_require_managed", lambda *a, **k: _Unit(UNIT))

        getattr(manager, action)(UNIT)

        assert any(call[:2] == ("systemctl", action) for call in runner.calls)


class _Unit:
    """What ``_require_managed`` answers: the unit and the file systemd is addressed by."""

    def __init__(self, unit: str) -> None:
        self.unit = unit
        self.unit_file = f"{unit}.service"
        self.exists = True
        self.managed = True


class TestTheSupervisorOfAMachineWithNoSystemd:
    @pytest.mark.parametrize("action", ["start", "restart"])
    def test_a_refusal_never_reaches_compose(
        self, action: str, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.deployers.compose_supervisor import ComposeSupervisor

        make_app(store, app)
        runner = Compose(str(app / "data"))
        set_runner(runner)

        def check(unit: str, **_: Any) -> None:
            raise ServiceError("stack.example.com was not started")

        monkeypatch.setattr(compose_gate, "check_before", check)

        with pytest.raises(ServiceError, match="was not started"):
            getattr(ComposeSupervisor(runner=runner), action)(UNIT)

        assert not [call for call in runner.calls if "up" in call]


class TestWhatTheApiSays:
    def test_a_start_held_back_is_a_conflict_with_its_code_and_the_ways_out(self) -> None:
        from noust.core.exceptions import StartHeldBackError
        from noust.web.api.deps import error_response, status_for

        error = compose_gate.Outcome(False, lines=("- service web mounts /",), domain=DOMAIN).error(
            StartHeldBackError
        )

        assert status_for(error) == 409
        body = json.loads(error_response(error).body)
        assert body["coded"]["code"] == "compose_gate.refused"
        assert body["error"] == "startheldbackerror"
        assert "service web mounts /" in body["hint"]
