# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long an install and a build may take, per application and per server (checklist A-48).

Until 3.4 the limits were two constants (1800 s and 2700 s). The build block's ``timeout`` gives
an application its own, ``deploy.install_timeout`` and ``deploy.build_timeout`` give the server's,
and a step that runs out of time says which setting to raise and that more time will not help a
build that is stuck.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_build_store, delivery
from noust.core.config import Config
from noust.core.exceptions import BuildError, ConfigError, DeploymentError, ValidationError
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.helpers.build_config import (
    MAX_TIMEOUT,
    MIN_TIMEOUT,
    BuildConfig,
    refuse_unsupported,
)
from noust.deployers.helpers.project_file import parse_build_document
from noust.deployers.nodejs import NodeJSDeployer
from tests.test_delivery_config import config  # noqa: F401

DOMAIN = "app.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """An isolated store with one Node application."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "app"
    root.mkdir()
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(root)))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """An empty configuration, and the default runner put back after --dry-run."""
    from noust.core.runner import set_runner

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    delivery.forget()
    yield
    Config.reset_instance()
    delivery.forget()
    set_runner(None)


def invoke(*args: str) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args))


class TestTheSchema:
    def test_the_key_is_read_as_seconds(self) -> None:
        build = parse_build_document("build:\n  timeout: 3600\n", source="operator")
        assert build.timeout == 3600
        assert build.declared_keys == ("timeout",)
        assert build.to_dict()["timeout"] == 3600

    def test_it_is_written_back_as_a_number(self) -> None:
        build = parse_build_document("build:\n  start: node a.js\n  timeout: 900\n", source="repo")
        assert build.to_mapping() == {"start": "node a.js", "timeout": 900}
        assert "timeout: 900" in build.to_document()

    def test_it_is_left_out_when_not_set(self) -> None:
        assert parse_build_document("build:\n  start: x\n", source="repo").timeout is None

    @pytest.mark.parametrize(
        "value", ["'soon'", "true", "12.5", "30", str(MIN_TIMEOUT - 1), str(MAX_TIMEOUT + 1), "-1"]
    )
    def test_a_value_that_is_not_a_sane_number_of_seconds_is_refused(self, value: str) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_build_document(f"build:\n  timeout: {value}\n", source="operator")
        assert caught.value.field == "build.timeout"
        assert caught.value.details

    def test_the_limits_of_the_range_are_accepted(self) -> None:
        for value in (MIN_TIMEOUT, MAX_TIMEOUT):
            build = parse_build_document(f"build:\n  timeout: {value}\n", source="operator")
            assert build.timeout == value

    @pytest.mark.parametrize("app_type", ["nodejs", "static", "php-fpm", "python", "go", "java"])
    def test_every_type_that_noust_builds_honours_it(self, app_type: str) -> None:
        refuse_unsupported(BuildConfig(timeout=600, source="operator"), app_type)

    def test_a_stack_honours_it_for_docker_compose_build(self) -> None:
        refuse_unsupported(BuildConfig(timeout=600, source="operator"), "docker-compose")

    def test_a_monorepo_built_by_its_workspaces_does_not(self) -> None:
        with pytest.raises(ValidationError) as caught:
            refuse_unsupported(BuildConfig(timeout=600, source="operator"), "monorepo")
        assert caught.value.field == "build"


class TestTheServerSettings:
    def test_the_defaults_are_the_two_constants_there_always_were(self) -> None:
        settings = delivery.DeliverySettings()
        assert (settings.install_timeout, settings.build_timeout) == (1800, 2700)

    def test_they_are_read_from_the_configuration(self, config: Config) -> None:  # noqa: F811
        config.set("deploy.build_timeout", "7200")
        config.set("deploy.install_timeout", "600")
        assert config.save() is True
        settings = delivery.current(refresh=True)
        assert (settings.install_timeout, settings.build_timeout) == (600, 7200)

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("deploy.build_timeout", "59"),
            ("deploy.build_timeout", "86401"),
            ("deploy.build_timeout", "an hour"),
            ("deploy.install_timeout", "0"),
        ],
    )
    def test_a_value_out_of_range_is_refused_where_it_is_written(
        self,
        config: Config,  # noqa: F811
        key: str,
        value: str,
    ) -> None:
        with pytest.raises(ConfigError):
            config.set(key, value)

    def test_a_hand_edited_value_that_does_not_hold_is_the_default(self) -> None:
        class Broken:
            def get(self, key: str, default: object = None) -> object:
                return {"deploy.build_timeout": "soon", "deploy.install_timeout": 5}.get(key)

        settings = delivery.DeliverySettings.read(Broken())  # type: ignore[arg-type]
        assert (settings.install_timeout, settings.build_timeout) == (1800, 2700)


def deployer(tmp_path: Path, runner: FakeRunner, document: str | None = None) -> NodeJSDeployer:
    """
    Args:
        tmp_path: Where the application is.
        runner: What runs its commands.
        document: The text of a ``noust.yaml``, when there is one.

    Returns:
        A configured Node deployer whose commands are scripted.
    """
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "tsc", "start": "node dist/index.js"}, "main": "x.js"})
    )
    (tmp_path / "package-lock.json").write_text("{}")
    if document is not None:
        (tmp_path / "noust.yaml").write_text(document)
    instance = NodeJSDeployer(runner=runner)
    instance.configure(DOMAIN, "src", app_path=tmp_path)
    return instance


class SlowRunner(FakeRunner):
    """A runner whose installs and builds never finish, and that remembers the deadline they had."""

    def __init__(self, *, hang: bool = True) -> None:
        super().__init__()
        self.hang = hang
        self.deadlines: list[int] = []

    def stream(
        self, argv: Any, *, on_line: Any, timeout: int = 300, **kwargs: Any
    ) -> CommandResult:  # type: ignore[override]
        self.deadlines.append(timeout)
        if self.hang:
            self._lookup(argv, None, kwargs.get("env"))
            return CommandResult(
                argv=tuple(argv), exit_code=124, stdout="", stderr="", timed_out=True
            )
        return super().stream(argv, on_line=on_line, timeout=timeout, **kwargs)


class TestWhatTheDeployerGives:
    def test_without_anything_it_is_the_servers_default(
        self,
        tmp_path: Path,
        config: Config,  # noqa: F811
    ) -> None:
        instance = deployer(tmp_path, FakeRunner())
        assert (instance.install_timeout(), instance.build_timeout()) == (1800, 2700)

    def test_the_server_setting_is_obeyed(self, tmp_path: Path, config: Config) -> None:  # noqa: F811
        config.set("deploy.build_timeout", "5400")
        config.save()
        delivery.forget()
        instance = deployer(tmp_path, FakeRunner())
        assert instance.build_timeout() == 5400
        assert instance.install_timeout() == 1800

    def test_the_applications_own_wins_for_both_steps(
        self,
        tmp_path: Path,
        config: Config,  # noqa: F811
    ) -> None:
        config.set("deploy.build_timeout", "5400")
        config.save()
        delivery.forget()
        instance = deployer(tmp_path, FakeRunner(), "build:\n  timeout: 900\n")
        assert (instance.install_timeout(), instance.build_timeout()) == (900, 900)

    def test_the_install_and_the_build_are_run_with_it(
        self,
        tmp_path: Path,
        config: Config,  # noqa: F811
    ) -> None:
        runner = SlowRunner(hang=False)
        instance = deployer(tmp_path, runner, "build:\n  timeout: 1234\n")

        instance.install_dependencies()
        instance.build()

        assert runner.deadlines == [1234, 1234]

    def test_without_a_setting_they_are_run_with_the_servers(
        self,
        tmp_path: Path,
        config: Config,  # noqa: F811
    ) -> None:
        runner = SlowRunner(hang=False)
        instance = deployer(tmp_path, runner)

        instance.install_dependencies()
        instance.build()

        assert runner.deadlines == [1800, 2700]

    def test_an_install_that_runs_out_of_time_says_what_to_raise(
        self,
        tmp_path: Path,
        config: Config,  # noqa: F811
    ) -> None:
        instance = deployer(tmp_path, SlowRunner(), "build:\n  timeout: 300\n")

        with pytest.raises(DeploymentError) as caught:
            instance.install_dependencies()

        assert "did not finish within 300s" in str(caught.value)
        details = caught.value.details or ""
        assert f"noust app build set {DOMAIN} --timeout 600" in details
        assert "noust config set deploy.install_timeout 600" in details
        assert "stuck" in details

    def test_a_build_that_runs_out_of_time_says_what_to_raise(
        self,
        tmp_path: Path,
        config: Config,  # noqa: F811
    ) -> None:
        instance = deployer(tmp_path, SlowRunner())
        instance.pre_install()

        with pytest.raises(BuildError) as caught:
            instance.build()

        assert "did not finish within 2700s" in str(caught.value)
        details = caught.value.details or ""
        assert "noust config set deploy.build_timeout 5400" in details

    def test_the_suggestion_never_goes_past_a_day(
        self,
        tmp_path: Path,
        config: Config,  # noqa: F811
    ) -> None:
        instance = deployer(tmp_path, FakeRunner(), "build:\n  timeout: 80000\n")
        assert f"--timeout {MAX_TIMEOUT}" in instance._timeout_hint("build", 80000)


class TestTheCommand:
    def test_set_stores_it_and_show_names_it(self, store: NoustStore, cli_config: None) -> None:
        result = invoke("app", "build", "set", DOMAIN, "--timeout", "3600", "--json")

        assert result.exit_code == 0, result.output
        shown = json.loads(result.output)
        assert shown["timeout"] == 3600
        assert shown["source"] == "operator"
        assert app_build_store.get_document(DOMAIN, store=store) == "build:\n  timeout: 3600\n"

        text = invoke("app", "build", "show", DOMAIN)
        assert "Timeout (seconds)" in text.output
        assert "3600" in text.output

    def test_it_is_added_to_what_the_operator_already_set(
        self, store: NoustStore, cli_config: None
    ) -> None:
        invoke("app", "build", "set", DOMAIN, "--start", "node a.js")

        invoke("app", "build", "set", DOMAIN, "--timeout", "900")

        document = app_build_store.get_document(DOMAIN, store=store) or ""
        assert "start: node a.js" in document
        assert "timeout: 900" in document

    def test_a_value_out_of_range_is_refused_before_anything_is_stored(
        self, store: NoustStore, cli_config: None
    ) -> None:
        result = invoke("app", "build", "set", DOMAIN, "--timeout", "5")

        assert result.exit_code != 0
        assert "60" in result.output
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_unset_removes_it(self, store: NoustStore, cli_config: None) -> None:
        invoke("app", "build", "set", DOMAIN, "--start", "node a.js", "--timeout", "900")

        result = invoke("app", "build", "unset", DOMAIN, "timeout")

        assert result.exit_code == 0, result.output
        assert app_build_store.get_document(DOMAIN, store=store) == "build:\n  start: node a.js\n"


class TestTheApi:
    def client(self) -> Any:
        from tests.test_build_surfaces import build_client

        return build_client(elevated=True)

    def test_it_is_set_and_read_back_as_a_number(self, store: NoustStore) -> None:
        response = self.client().put(f"/api/apps/{DOMAIN}/build", json={"timeout": 3600})

        assert response.status_code == 200, response.text
        assert response.json()["timeout"] == 3600
        assert self.client().get(f"/api/apps/{DOMAIN}/build").json()["timeout"] == 3600

    @pytest.mark.parametrize("value", [10, 100000, "soon"])
    def test_a_value_out_of_range_is_refused(self, store: NoustStore, value: Any) -> None:
        response = self.client().put(f"/api/apps/{DOMAIN}/build", json={"timeout": value})

        assert response.status_code in (400, 422)
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_a_block_with_only_a_timeout_is_a_block(self, store: NoustStore) -> None:
        response = self.client().put(f"/api/apps/{DOMAIN}/build", json={"timeout": 600})
        assert response.status_code == 200
        assert response.json()["source"] == "operator"
