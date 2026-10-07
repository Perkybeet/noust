"""
Tests for one-off commands: the same account and secrets as a terminal, no shell
between the command and the program, a deadline, bounded output, and a trail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.terminal import execution, targets
from noust.terminal.execution import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_BYTES,
    clamp_timeout,
    run_exec,
)

SECRET = "postgres://shop:hunter2-hunter2@db/shop"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_unit(monkeypatch):
    monkeypatch.setattr(targets, "_unit_variables", lambda app: {"PORT": "3001"})


def add_app(store: NoustStore, tmp_path: Path, **fields: Any) -> App:
    values: dict[str, Any] = {
        "domain": "shop.example.com",
        "app_type": "nodejs",
        "app_path": str(tmp_path / "shop"),
        "status": "running",
        "layout": "releases",
        "identity": "noust-app-shop",
    }
    values.update(fields)
    app = App(**values)
    store.create_app(app)
    return store.get_app(app.domain) or app


class Lines:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


class TestTheTimeLimit:
    def test_no_time_asked_is_the_default(self):
        assert clamp_timeout(None) == DEFAULT_TIMEOUT_SECONDS

    def test_a_time_inside_the_ceiling_is_kept(self):
        assert clamp_timeout(900) == 900

    def test_a_time_over_the_ceiling_is_refused_with_how_to_raise_it(self):
        with pytest.raises(ValidationError) as refused:
            clamp_timeout(10**6)

        assert refused.value.field == "timeout"
        assert "terminal.exec.max_seconds" in str(refused.value.details)

    @pytest.mark.parametrize("bad", [0, -4, True])
    def test_a_time_that_is_not_a_positive_number_is_refused(self, bad):
        with pytest.raises(ValidationError):
            clamp_timeout(bad)


class TestRunningACommand:
    def test_the_command_runs_as_the_application_in_its_sandbox(self, store, tmp_path):
        add_app(store, tmp_path)
        runner = FakeRunner().script(["rails", "runner"], stdout="42\n")
        lines = Lines()

        result = run_exec(
            "shop.example.com", ["rails", "runner", "puts 6*7"], on_line=lines, runner=runner
        )

        assert lines.lines == ["42"]
        assert result.success and result.exit_code == 0
        argv, spec = runner.sandboxed[0]
        assert argv == ("rails", "runner", "puts 6*7")
        assert spec.user == "noust-app-shop"
        assert spec.interactive is False
        assert runner.envs[0] is not None and runner.envs[0]["PORT"] == "3001"

    def test_the_command_is_given_to_the_program_as_it_is(self, store, tmp_path):
        add_app(store, tmp_path)
        runner = FakeRunner()

        run_exec("shop.example.com", ["echo", "a; rm -rf /; $(id)"], on_line=Lines(), runner=runner)

        assert runner.sandboxed[0][0] == ("echo", "a; rm -rf /; $(id)")
        # Nothing between the program and its arguments: no `sh -c`.
        assert "sh" not in runner.sandboxed[0][0] and "-c" not in runner.sandboxed[0][0]

    def test_the_deadline_is_the_one_asked_for_and_the_runners(self, store, tmp_path):
        add_app(store, tmp_path)
        runner = FakeRunner()

        run_exec("shop.example.com", ["sleep", "1"], timeout=45, on_line=Lines(), runner=runner)

        assert any(f"RuntimeMaxSec={45 + 60}" in part for part in runner.calls[0])

    def test_a_command_that_fails_is_a_result_with_its_status(self, store, tmp_path):
        add_app(store, tmp_path)
        runner = FakeRunner().script(["false"], exit_code=3, stdout="boom\n")
        lines = Lines()

        result = run_exec("shop.example.com", ["false"], on_line=lines, runner=runner)

        assert result.exit_code == 3 and not result.success
        assert lines.lines == ["boom"]

    def test_a_secret_the_command_prints_is_not_handed_on(self, store, tmp_path):
        add_app(store, tmp_path, env_vars={"DATABASE_URL": SECRET})
        runner = FakeRunner().script(["printenv"], stdout=f"DATABASE_URL={SECRET}\nPORT=3001\n")
        lines = Lines()

        run_exec("shop.example.com", ["printenv"], on_line=lines, runner=runner)

        assert all("hunter2-hunter2" not in line for line in lines.lines)
        assert "PORT=3001" in lines.lines

    def test_output_beyond_the_bound_is_cut_once_and_said(self, store, tmp_path):
        add_app(store, tmp_path)
        line = "x" * 1023
        count = MAX_OUTPUT_BYTES // 1024 + 50
        runner = FakeRunner().script(["yes"], stdout="\n".join([line] * count) + "\n")
        lines = Lines()

        result = run_exec("shop.example.com", ["yes"], on_line=lines, runner=runner)

        assert result.output_cut is True
        assert lines.lines[-1].startswith("[output cut")
        assert sum(1 for entry in lines.lines if entry.startswith("[output cut")) == 1
        assert result.output_bytes <= MAX_OUTPUT_BYTES

    def test_an_application_that_is_not_there_is_not_found(self, store, tmp_path):
        with pytest.raises(ValidationError, match="not found"):
            run_exec("nope.example.com", ["ls"], on_line=Lines(), runner=FakeRunner())

    def test_a_static_site_has_nothing_to_run_a_command_in(self, store, tmp_path):
        add_app(store, tmp_path, app_type="static", is_static=True)

        with pytest.raises(ValidationError, match="static"):
            run_exec("shop.example.com", ["ls"], on_line=Lines(), runner=FakeRunner())

    def test_an_empty_command_is_refused_before_anything_runs(self, store, tmp_path):
        add_app(store, tmp_path)
        runner = FakeRunner()

        with pytest.raises(ValidationError):
            run_exec("shop.example.com", [], on_line=Lines(), runner=runner)

        assert runner.calls == []


class TestInAStack:
    def test_the_command_goes_through_docker_exec_to_the_service_asked_for(self, store, tmp_path):
        add_app(store, tmp_path, app_type="docker-compose", identity=None, compose_project="shop")
        runner = FakeRunner().script(["docker", "ps"], stdout="aaa|web|running\nbbb|db|running\n")
        runner.script(["docker", "exec"], stdout="ok\n")
        lines = Lines()

        result = run_exec(
            "shop.example.com",
            ["rails", "console"],
            service="web",
            on_line=lines,
            runner=runner,
        )

        assert runner.calls[-1] == ("docker", "exec", "aaa", "rails", "console")
        assert result.target == "shop.example.com (web)"
        assert lines.lines == ["ok"]
        assert runner.sandboxed == []


class TestTheTrail:
    def test_it_is_on_record_when_it_starts_and_when_it_ends(self, store, tmp_path):
        add_app(store, tmp_path)
        runner = FakeRunner().script(["rake"], exit_code=1)

        run_exec("shop.example.com", ["rake", "db:migrate"], on_line=Lines(), runner=runner)

        events = audit.get_log().read(action="terminal.exec")
        started, finished = events[1], events[0]  # newest first
        assert started["details"]["stage"] == "started"
        assert started["details"]["command"] == "rake db:migrate"
        assert started["details"]["account"] == "noust-app-shop"
        assert started["resource"] == "app:shop.example.com"
        assert finished["details"]["stage"] == "finished"
        assert finished["result"] == "failure"
        assert finished["details"]["exit_code"] == 1

    def test_the_command_in_the_trail_hides_what_noust_knows_is_secret(self, store, tmp_path):
        add_app(store, tmp_path, env_vars={"DATABASE_URL": SECRET})
        runner = FakeRunner()

        run_exec(
            "shop.example.com",
            ["psql", SECRET, "-c", "select 1"],
            on_line=Lines(),
            runner=runner,
        )

        for event in audit.get_log().read(action="terminal.exec"):
            assert "hunter2-hunter2" not in str(event)

    def test_a_long_command_is_cut_in_the_trail(self, store, tmp_path):
        add_app(store, tmp_path)

        run_exec("shop.example.com", ["echo", "y" * 5000], on_line=Lines(), runner=FakeRunner())

        command = audit.get_log().read(action="terminal.exec")[0]["details"]["command"]
        assert len(command) <= execution.MAX_COMMAND_CHARS + 3
        assert command.endswith("...")
